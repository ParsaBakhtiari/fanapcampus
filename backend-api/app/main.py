import json
import logging
import time
from contextlib import asynccontextmanager
from decimal import Decimal
from uuid import uuid4

from fastapi import Depends, FastAPI, File, Header, HTTPException, Request, Response, UploadFile
from fastapi.responses import JSONResponse
from prometheus_client import CONTENT_TYPE_LATEST, Counter, Histogram, generate_latest
from redis import Redis
from redis.exceptions import RedisError
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from . import orders_client, storage
from .config import get_settings
from .db import engine, get_db
from .logs import log_extra, setup_logging
from .models import Product, User
from .schemas import (
    CartItemIn,
    CartLine,
    CartOut,
    CheckoutRequest,
    LoginRequest,
    ProductCreate,
    ProductOut,
    ProductUpdate,
    RegisterRequest,
    TokenResponse,
    UserOut,
)
from .security import create_token, current_user, hash_password, require_admin, verify_password

settings = get_settings()
log = setup_logging(settings.service_name, settings.log_level)
# protocol=2 (RESP2): DCS / Redis 5.x does not support RESP3 ("HELLO 3").
redis = Redis.from_url(settings.redis_url, protocol=2, decode_responses=True, socket_connect_timeout=2, socket_timeout=2)

REQUESTS = Counter("http_requests_total", "HTTP requests", ["method", "route", "status"])
LATENCY = Histogram("http_request_duration_seconds", "HTTP latency", ["route"])
CHECKOUTS = Counter("shop_checkouts_total", "Checkout attempts", ["result"])

CATALOG_KEY = "catalog:v1"
IMAGE_TYPES = {"image/jpeg": "jpg", "image/png": "png", "image/webp": "webp"}


@asynccontextmanager
async def lifespan(_: FastAPI):
    log.info("backend-api starting", **log_extra(environment=settings.environment))
    yield
    engine.dispose()
    redis.close()


app = FastAPI(
    title="Pardis Shop - Backend API",
    version="1.0.0",
    lifespan=lifespan,
    docs_url="/api/docs" if settings.environment != "production" else None,
    openapi_url="/api/openapi.json" if settings.environment != "production" else None,
    redoc_url=None,
)


# --------------------------------------------------------------------------- middleware
@app.middleware("http")
async def observe(request: Request, call_next):
    request_id = request.headers.get("x-request-id") or str(uuid4())
    request.state.request_id = request_id
    start = time.perf_counter()
    status = 500
    try:
        response = await call_next(request)
        status = response.status_code
    finally:
        route = request.scope.get("route")
        route_path = getattr(route, "path", "unmatched")
        elapsed = time.perf_counter() - start
        REQUESTS.labels(request.method, route_path, status).inc()
        LATENCY.labels(route_path).observe(elapsed)
        if not route_path.startswith(("/healthz", "/metrics")):
            log.info(
                "request",
                **log_extra(
                    request_id=request_id,
                    method=request.method,
                    path=request.url.path,
                    status=status,
                    duration_ms=round(elapsed * 1000, 1),
                    client=request.headers.get("x-forwarded-for", request.client.host if request.client else ""),
                ),
            )
    response.headers["X-Request-ID"] = request_id
    return response


@app.exception_handler(Exception)
async def unhandled(request: Request, exc: Exception):
    log.exception("unhandled error", **log_extra(request_id=getattr(request.state, "request_id", "")))
    return JSONResponse(status_code=500, content={"detail": "internal server error"})


# --------------------------------------------------------------------------- health
@app.get("/healthz/live", include_in_schema=False)
def live():
    return {"status": "ok"}


@app.get("/healthz/ready", include_in_schema=False)
def ready(db: Session = Depends(get_db)):
    """Only checks what this pod needs to serve traffic (RDS + DCS)."""
    try:
        db.execute(text("SELECT 1"))
        redis.ping()
    except Exception as exc:
        log.warning("readiness failed: %s", exc)
        raise HTTPException(503, "dependency unavailable") from exc
    return {"status": "ready"}


@app.get("/healthz/deps", include_in_schema=False)
def deps(db: Session = Depends(get_db)):
    """Troubleshooting endpoint: status of every dependency. Cluster-internal only."""
    result: dict[str, str] = {}
    try:
        db.execute(text("SELECT 1"))
        result["mysql"] = "ok"
    except Exception as exc:
        result["mysql"] = f"error: {exc.__class__.__name__}: {exc}"
    try:
        redis.ping()
        result["redis"] = "ok"
    except Exception as exc:
        result["redis"] = f"error: {exc.__class__.__name__}: {exc}"
    try:
        storage.obs_client().head_bucket(Bucket=settings.obs_bucket_images)
        result["obs_images_bucket"] = "ok"
    except Exception as exc:
        result["obs_images_bucket"] = f"error: {exc.__class__.__name__}: {exc}"
    result["order_service"] = "ok" if orders_client.ping() else "error: unreachable"
    return result


@app.get("/metrics", include_in_schema=False)
def metrics():
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)


# --------------------------------------------------------------------------- auth
@app.post("/api/v1/auth/register", response_model=UserOut, status_code=201)
def register(payload: RegisterRequest, db: Session = Depends(get_db)):
    user = User(email=payload.email.lower(), full_name=payload.full_name, password_hash=hash_password(payload.password))
    db.add(user)
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(409, "email already registered") from exc
    return user


def _rate_limit_login(email: str) -> None:
    key = f"ratelimit:login:{email}"
    try:
        attempts = redis.incr(key)
        if attempts == 1:
            redis.expire(key, 60)
    except RedisError:
        return  # fail open: login still works if DCS is down
    if attempts > settings.login_attempts_per_minute:
        raise HTTPException(429, "too many login attempts, try again in a minute")


@app.post("/api/v1/auth/login", response_model=TokenResponse)
def login(payload: LoginRequest, db: Session = Depends(get_db)):
    email = payload.email.lower()
    _rate_limit_login(email)
    user = db.scalar(select(User).where(User.email == email))
    if user is None or not user.is_active or not verify_password(payload.password, user.password_hash):
        raise HTTPException(401, "invalid email or password")
    return TokenResponse(access_token=create_token(user), expires_in=settings.jwt_ttl_minutes * 60)


@app.get("/api/v1/auth/me", response_model=UserOut)
def me(user: User = Depends(current_user)):
    return user


# --------------------------------------------------------------------------- catalog
def _product_out(p: Product) -> ProductOut:
    return ProductOut(
        id=p.id, sku=p.sku, name=p.name, description=p.description, price=p.price,
        stock=p.stock, has_image=bool(p.image_key), is_active=p.is_active,
    )


def _invalidate_catalog() -> None:
    try:
        redis.delete(CATALOG_KEY)
    except RedisError:
        log.warning("could not invalidate catalog cache")


@app.get("/api/v1/products", response_model=list[ProductOut])
def list_products(response: Response, db: Session = Depends(get_db)):
    try:
        cached = redis.get(CATALOG_KEY)
        if cached:
            response.headers["X-Cache"] = "HIT"
            return json.loads(cached)
    except RedisError:
        log.warning("catalog cache read failed, falling back to database")
    rows = db.scalars(select(Product).where(Product.is_active.is_(True)).order_by(Product.name)).all()
    data = [_product_out(p).model_dump(mode="json") for p in rows]
    try:
        redis.setex(CATALOG_KEY, settings.catalog_cache_seconds, json.dumps(data))
    except RedisError:
        pass
    response.headers["X-Cache"] = "MISS"
    return data


@app.get("/api/v1/products/{product_id}", response_model=ProductOut)
def get_product(product_id: str, db: Session = Depends(get_db)):
    p = db.get(Product, product_id)
    if p is None or not p.is_active:
        raise HTTPException(404, "product not found")
    return _product_out(p)


@app.get("/api/v1/products/{product_id}/image")
def product_image(product_id: str, db: Session = Depends(get_db)):
    p = db.get(Product, product_id)
    if p is None or not p.image_key:
        raise HTTPException(404, "image not found")
    try:
        body, content_type = storage.get_object(settings.obs_bucket_images, p.image_key)
    except Exception as exc:
        log.exception("OBS read failed", **log_extra(key=p.image_key))
        raise HTTPException(503, "object storage unavailable") from exc
    return Response(body, media_type=content_type, headers={"Cache-Control": "public, max-age=300"})


# --------------------------------------------------------------------------- admin
@app.get("/api/v1/admin/products", response_model=list[ProductOut])
def admin_list_products(_: User = Depends(require_admin), db: Session = Depends(get_db)):
    return [_product_out(p) for p in db.scalars(select(Product).order_by(Product.name)).all()]


@app.post("/api/v1/admin/products", response_model=ProductOut, status_code=201)
def admin_create_product(payload: ProductCreate, _: User = Depends(require_admin), db: Session = Depends(get_db)):
    p = Product(**payload.model_dump())
    db.add(p)
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(409, "SKU already exists") from exc
    _invalidate_catalog()
    return _product_out(p)


@app.patch("/api/v1/admin/products/{product_id}", response_model=ProductOut)
def admin_update_product(product_id: str, payload: ProductUpdate, _: User = Depends(require_admin), db: Session = Depends(get_db)):
    p = db.get(Product, product_id)
    if p is None:
        raise HTTPException(404, "product not found")
    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(p, field, value)
    db.commit()
    _invalidate_catalog()
    return _product_out(p)


@app.post("/api/v1/admin/products/{product_id}/image", response_model=ProductOut)
def admin_upload_image(
    product_id: str,
    file: UploadFile = File(...),
    _: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    p = db.get(Product, product_id)
    if p is None:
        raise HTTPException(404, "product not found")
    if file.content_type not in IMAGE_TYPES:
        raise HTTPException(415, "only JPEG, PNG or WebP images are allowed")
    body = file.file.read(settings.max_image_bytes + 1)
    if len(body) > settings.max_image_bytes:
        raise HTTPException(413, "image is larger than 5 MiB")
    key = f"images/products/{p.id}/{uuid4()}.{IMAGE_TYPES[file.content_type]}"
    try:
        storage.put_object(settings.obs_bucket_images, key, body, file.content_type)
    except Exception as exc:
        log.exception("OBS upload failed", **log_extra(key=key))
        raise HTTPException(503, "object storage unavailable") from exc
    p.image_key = key
    db.commit()
    _invalidate_catalog()
    return _product_out(p)


@app.get("/api/v1/admin/orders")
def admin_orders(_: User = Depends(require_admin)):
    return orders_client.call("GET", "/internal/orders", params={"all": "true"}).json()


# --------------------------------------------------------------------------- cart (DCS)
def _cart_key(user_id: str) -> str:
    return f"cart:{user_id}"


def _read_cart(user_id: str) -> dict[str, int]:
    try:
        raw = redis.hgetall(_cart_key(user_id))
    except RedisError as exc:
        raise HTTPException(503, "cart storage unavailable") from exc
    return {pid: int(qty) for pid, qty in raw.items()}


def _cart_view(cart: dict[str, int], db: Session) -> CartOut:
    lines: list[CartLine] = []
    total = Decimal("0")
    products = {p.id: p for p in db.scalars(select(Product).where(Product.id.in_(list(cart)))).all()} if cart else {}
    for pid, qty in cart.items():
        p = products.get(pid)
        if p is None:
            continue
        line_total = p.price * qty
        available = p.is_active and p.stock >= qty
        lines.append(CartLine(product_id=pid, name=p.name, unit_price=p.price, quantity=qty, line_total=line_total, available=available))
        total += line_total
    return CartOut(items=lines, total=total, currency=settings.currency)


@app.get("/api/v1/cart", response_model=CartOut)
def get_cart(user: User = Depends(current_user), db: Session = Depends(get_db)):
    return _cart_view(_read_cart(user.id), db)


@app.put("/api/v1/cart", response_model=CartOut)
def set_cart_item(item: CartItemIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
    p = db.get(Product, item.product_id)
    if p is None or not p.is_active:
        raise HTTPException(404, "product not found")
    if p.stock < item.quantity:
        raise HTTPException(409, f"only {p.stock} left in stock")
    key = _cart_key(user.id)
    try:
        redis.hset(key, item.product_id, item.quantity)
        redis.expire(key, settings.cart_ttl_seconds)
    except RedisError as exc:
        raise HTTPException(503, "cart storage unavailable") from exc
    return _cart_view(_read_cart(user.id), db)


@app.delete("/api/v1/cart/items/{product_id}", response_model=CartOut)
def remove_cart_item(product_id: str, user: User = Depends(current_user), db: Session = Depends(get_db)):
    try:
        redis.hdel(_cart_key(user.id), product_id)
    except RedisError as exc:
        raise HTTPException(503, "cart storage unavailable") from exc
    return _cart_view(_read_cart(user.id), db)


@app.delete("/api/v1/cart", status_code=204)
def clear_cart(user: User = Depends(current_user)):
    try:
        redis.delete(_cart_key(user.id))
    except RedisError as exc:
        raise HTTPException(503, "cart storage unavailable") from exc
    return Response(status_code=204)


# --------------------------------------------------------------------------- checkout & orders
def _restock(db: Session, items: list[dict]) -> None:
    for item in items:
        p = db.get(Product, item["product_id"], with_for_update=True)
        if p is not None:
            p.stock += int(item["quantity"])
    db.commit()
    _invalidate_catalog()


@app.post("/api/v1/checkout", status_code=201)
def checkout(
    payload: CheckoutRequest,
    idempotency_key: str = Header(alias="Idempotency-Key", min_length=8, max_length=64),
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    # 1. Same Idempotency-Key already produced an order -> return it (safe retries).
    existing = orders_client.call(
        "GET", "/internal/orders/by-key", params={"user_id": user.id, "key": idempotency_key}
    ).json()
    if existing.get("order"):
        return existing["order"]

    # 2. Lock this checkout in DCS so double clicks cannot run twice in parallel.
    lock_key = f"lock:checkout:{user.id}:{idempotency_key}"
    try:
        if not redis.set(lock_key, "1", nx=True, ex=60):
            raise HTTPException(409, "checkout already in progress")
    except RedisError as exc:
        raise HTTPException(503, "cart storage unavailable") from exc

    try:
        cart = _read_cart(user.id)
        if not cart:
            raise HTTPException(400, "cart is empty")

        # 3. Reserve stock in RDS (row locks keep stock consistent across replicas).
        products = {
            p.id: p
            for p in db.scalars(select(Product).where(Product.id.in_(list(cart))).with_for_update()).all()
        }
        items = []
        for pid, qty in cart.items():
            p = products.get(pid)
            if p is None or not p.is_active:
                db.rollback()
                raise HTTPException(409, "a product in your cart is no longer available")
            if p.stock < qty:
                db.rollback()
                raise HTTPException(409, f"not enough stock for {p.name} (left: {p.stock})")
            p.stock -= qty
            items.append({"product_id": p.id, "sku": p.sku, "name": p.name, "unit_price": str(p.price), "quantity": qty})
        db.commit()
        _invalidate_catalog()

        # 4. Create the order in Order Service. On failure, give the stock back.
        try:
            order = orders_client.call(
                "POST",
                "/internal/orders",
                json={
                    "user_id": user.id,
                    "user_email": user.email,
                    "idempotency_key": idempotency_key,
                    "currency": settings.currency,
                    "shipping_address": payload.shipping_address,
                    "items": items,
                },
            ).json()
        except Exception:
            CHECKOUTS.labels("order_service_failed").inc()
            log.error("order creation failed, restocking", **log_extra(user_id=user.id))
            _restock(db, items)
            raise

        if order.get("duplicate"):
            # Another request with the same key won the race: undo our reservation.
            _restock(db, items)

        try:
            redis.delete(_cart_key(user.id))
        except RedisError:
            log.warning("could not clear cart after checkout", **log_extra(user_id=user.id))
        CHECKOUTS.labels("success").inc()
        log.info("order placed", **log_extra(order_id=order["id"], user_id=user.id, total=order["total"]))
        return order
    finally:
        try:
            redis.delete(lock_key)
        except RedisError:
            pass


@app.get("/api/v1/orders")
def my_orders(user: User = Depends(current_user)):
    return orders_client.call("GET", "/internal/orders", params={"user_id": user.id}).json()


@app.get("/api/v1/orders/{order_id}")
def my_order(order_id: str, user: User = Depends(current_user)):
    return orders_client.call("GET", f"/internal/orders/{order_id}", params={"user_id": user.id}).json()


@app.post("/api/v1/orders/{order_id}/cancel")
def cancel_order(order_id: str, user: User = Depends(current_user), db: Session = Depends(get_db)):
    result = orders_client.call("POST", f"/internal/orders/{order_id}/cancel", params={"user_id": user.id}).json()
    if result.get("restock"):
        _restock(db, result["order"]["items"])
    return result["order"]


@app.get("/api/v1/orders/{order_id}/invoice")
def order_invoice(order_id: str, user: User = Depends(current_user)):
    response = orders_client.call("GET", f"/internal/orders/{order_id}/invoice", params={"user_id": user.id})
    return Response(
        response.content,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="invoice-{order_id}.pdf"', "Cache-Control": "no-store"},
    )
