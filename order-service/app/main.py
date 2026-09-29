import hmac
import secrets
import time
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from decimal import Decimal
from uuid import uuid4

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request, Response
from fastapi.responses import JSONResponse
from prometheus_client import CONTENT_TYPE_LATEST, Counter, Histogram, generate_latest
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from . import storage
from .config import get_settings
from .db import engine, get_db
from .invoice import build_invoice
from .logs import log_extra, setup_logging
from .models import Order, OrderItem, OrderStatus
from .schemas import OrderCreate, OrderOut

settings = get_settings()
log = setup_logging(settings.service_name, settings.log_level)

REQUESTS = Counter("http_requests_total", "HTTP requests", ["method", "route", "status"])
LATENCY = Histogram("http_request_duration_seconds", "HTTP latency", ["route"])
ORDERS = Counter("shop_orders_total", "Orders by event", ["event"])
INVOICE_FAILURES = Counter("shop_invoice_upload_failures_total", "Invoice uploads to OBS that failed")


@asynccontextmanager
async def lifespan(_: FastAPI):
    log.info("order-service starting", **log_extra(environment=settings.environment))
    yield
    engine.dispose()


app = FastAPI(title="Pardis Shop - Order Service", version="1.0.0", lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)


@app.middleware("http")
async def observe(request: Request, call_next):
    request_id = request.headers.get("x-request-id") or str(uuid4())
    start = time.perf_counter()
    status = 500
    try:
        response = await call_next(request)
        status = response.status_code
    finally:
        route_path = getattr(request.scope.get("route"), "path", "unmatched")
        elapsed = time.perf_counter() - start
        REQUESTS.labels(request.method, route_path, status).inc()
        LATENCY.labels(route_path).observe(elapsed)
        if not route_path.startswith(("/healthz", "/metrics")):
            log.info("request", **log_extra(request_id=request_id, method=request.method, path=request.url.path,
                                             status=status, duration_ms=round(elapsed * 1000, 1)))
    response.headers["X-Request-ID"] = request_id
    return response


@app.exception_handler(Exception)
async def unhandled(_: Request, exc: Exception):
    log.exception("unhandled error")
    return JSONResponse(status_code=500, content={"detail": "internal server error"})


def internal_only(x_internal_token: str = Header(default="")) -> None:
    if not hmac.compare_digest(x_internal_token, settings.internal_token):
        raise HTTPException(401, "invalid internal token")


# --------------------------------------------------------------------------- health
@app.get("/healthz/live", include_in_schema=False)
def live():
    return {"status": "ok"}


@app.get("/healthz/ready", include_in_schema=False)
def ready(db: Session = Depends(get_db)):
    try:
        db.execute(text("SELECT 1"))
    except Exception as exc:
        log.warning("readiness failed: %s", exc)
        raise HTTPException(503, "database unavailable") from exc
    return {"status": "ready"}


@app.get("/healthz/deps", include_in_schema=False)
def deps(db: Session = Depends(get_db)):
    result: dict[str, str] = {}
    try:
        db.execute(text("SELECT 1"))
        result["mysql"] = "ok"
    except Exception as exc:
        result["mysql"] = f"error: {exc.__class__.__name__}: {exc}"
    try:
        storage.obs_client().head_bucket(Bucket=settings.obs_bucket_invoices)
        result["obs_invoices_bucket"] = "ok"
    except Exception as exc:
        result["obs_invoices_bucket"] = f"error: {exc.__class__.__name__}: {exc}"
    return result


@app.get("/metrics", include_in_schema=False)
def metrics():
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)


# --------------------------------------------------------------------------- helpers
def to_out(order: Order, duplicate: bool = False) -> dict:
    out = OrderOut(
        id=order.id, number=order.number, user_id=order.user_id, user_email=order.user_email,
        status=order.status.value, currency=order.currency, total=order.total,
        shipping_address=order.shipping_address, has_invoice=bool(order.invoice_key),
        created_at=order.created_at, items=order.items, duplicate=duplicate,
    )
    return out.model_dump(mode="json")


def _find(db: Session, order_id: str, user_id: str | None) -> Order:
    query = select(Order).where(Order.id == order_id)
    if user_id is not None:
        query = query.where(Order.user_id == user_id)
    order = db.scalar(query)
    if order is None:
        raise HTTPException(404, "order not found")
    return order


def _render_invoice(order: Order) -> bytes:
    created = order.created_at if order.created_at.tzinfo else order.created_at.replace(tzinfo=timezone.utc)
    return build_invoice(
        settings.shop_name, order.number, created, order.user_email, order.shipping_address, order.currency,
        [(i.sku, i.name, i.quantity, Decimal(i.unit_price)) for i in order.items], Decimal(order.total),
    )


def _upload_invoice(order: Order) -> str | None:
    """Store invoice at invoices/{year}/{month}/{number}.pdf (layout from the Phase 1 guide)."""
    created = order.created_at
    key = f"invoices/{created:%Y}/{created:%m}/{order.number}.pdf"
    try:
        storage.put_object(settings.obs_bucket_invoices, key, _render_invoice(order), "application/pdf")
        return key
    except Exception:
        INVOICE_FAILURES.inc()
        log.exception("invoice upload failed", **log_extra(order_id=order.id, key=key))
        return None


# --------------------------------------------------------------------------- internal API (Backend API only)
@app.post("/internal/orders", status_code=201, dependencies=[Depends(internal_only)])
def create_order(payload: OrderCreate, db: Session = Depends(get_db)):
    existing = db.scalar(select(Order).where(Order.user_id == payload.user_id, Order.idempotency_key == payload.idempotency_key))
    if existing:
        return to_out(existing, duplicate=True)

    now = datetime.now(timezone.utc)
    order = Order(
        number=f"PS-{now:%Y%m%d}-{secrets.token_hex(3).upper()}",
        user_id=payload.user_id,
        user_email=payload.user_email,
        idempotency_key=payload.idempotency_key,
        currency=payload.currency.upper(),
        shipping_address=payload.shipping_address,
        total=sum((i.unit_price * i.quantity for i in payload.items), Decimal("0")),
        created_at=now,
        items=[OrderItem(**i.model_dump()) for i in payload.items],
    )
    db.add(order)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        existing = db.scalar(select(Order).where(Order.user_id == payload.user_id, Order.idempotency_key == payload.idempotency_key))
        if existing:
            return to_out(existing, duplicate=True)
        raise
    ORDERS.labels("created").inc()

    key = _upload_invoice(order)
    if key:
        order.invoice_key = key
        db.commit()
    log.info("order created", **log_extra(order_id=order.id, number=order.number, total=str(order.total)))
    return to_out(order)


@app.get("/internal/orders/by-key", dependencies=[Depends(internal_only)])
def order_by_key(user_id: str, key: str, db: Session = Depends(get_db)):
    order = db.scalar(select(Order).where(Order.user_id == user_id, Order.idempotency_key == key))
    return {"order": to_out(order) if order else None}


@app.get("/internal/orders", dependencies=[Depends(internal_only)])
def list_orders(user_id: str | None = None, all: bool = Query(default=False), db: Session = Depends(get_db)):
    if user_id is None and not all:
        raise HTTPException(400, "user_id or all=true is required")
    query = select(Order).order_by(Order.created_at.desc()).limit(200)
    if user_id is not None:
        query = query.where(Order.user_id == user_id)
    return [to_out(o) for o in db.scalars(query).all()]


@app.get("/internal/orders/{order_id}", dependencies=[Depends(internal_only)])
def get_order(order_id: str, user_id: str | None = None, db: Session = Depends(get_db)):
    return to_out(_find(db, order_id, user_id))


@app.post("/internal/orders/{order_id}/cancel", dependencies=[Depends(internal_only)])
def cancel_order(order_id: str, user_id: str, db: Session = Depends(get_db)):
    order = db.scalar(select(Order).where(Order.id == order_id, Order.user_id == user_id).with_for_update())
    if order is None:
        raise HTTPException(404, "order not found")
    if order.status == OrderStatus.cancelled:
        return {"order": to_out(order), "restock": False}
    order.status = OrderStatus.cancelled
    db.commit()
    ORDERS.labels("cancelled").inc()
    log.info("order cancelled", **log_extra(order_id=order.id))
    return {"order": to_out(order), "restock": True}


@app.get("/internal/orders/{order_id}/invoice", dependencies=[Depends(internal_only)])
def get_invoice(order_id: str, user_id: str | None = None, db: Session = Depends(get_db)):
    order = _find(db, order_id, user_id)
    if order.invoice_key:
        try:
            return Response(storage.get_object(settings.obs_bucket_invoices, order.invoice_key), media_type="application/pdf")
        except Exception:
            log.exception("invoice read from OBS failed, rendering on the fly", **log_extra(order_id=order.id))
    else:
        key = _upload_invoice(order)  # retry an upload that failed at checkout time
        if key:
            order.invoice_key = key
            db.commit()
    return Response(_render_invoice(order), media_type="application/pdf")
