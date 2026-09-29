"""One-shot schema + bootstrap job.

Run as a Kubernetes Job (k8s/10-migrate-jobs.yaml) or `python -m app.migrate`.
Safe to run many times: it only creates what is missing."""
import os
import sys
import time
from decimal import Decimal

from sqlalchemy import select, text

from .db import Base, SessionLocal, engine
from .logs import setup_logging
from .models import Product, User
from .security import hash_password

log = setup_logging("backend-api-migrate", "INFO")

DEMO_PRODUCTS = [
    ("CLOUD-TEE", "Cloud T-Shirt", "Soft cotton tee with the cloud logo.", "24.90", 50),
    ("CLOUD-MUG", "Cloud Mug", "Ceramic mug, 350 ml.", "12.50", 80),
    ("CLOUD-CAP", "Cloud Cap", "Adjustable baseball cap.", "18.00", 40),
    ("CLOUD-NOTE", "Cloud Notebook", "A5 dotted notebook, 120 pages.", "9.90", 120),
]


def wait_for_db(retries: int = 30) -> None:
    for attempt in range(1, retries + 1):
        try:
            with engine.connect() as conn:
                conn.execute(text("SELECT 1"))
            return
        except Exception as exc:
            log.warning(f"database not ready (attempt {attempt}/{retries}): {exc}")
            time.sleep(2)
    log.error("database never became ready")
    sys.exit(1)


def main() -> None:
    wait_for_db()
    Base.metadata.create_all(engine)
    log.info("schema ready")

    with SessionLocal() as db:
        email = os.getenv("ADMIN_EMAIL", "").strip().lower()
        password = os.getenv("ADMIN_PASSWORD", "")
        if email and password:
            if db.scalar(select(User).where(User.email == email)) is None:
                db.add(User(email=email, full_name="Administrator", password_hash=hash_password(password), role="admin"))
                db.commit()
                log.info(f"admin user created: {email}")
            else:
                log.info("admin user already exists")
        else:
            log.warning("ADMIN_EMAIL / ADMIN_PASSWORD not set: no admin user created")

        if os.getenv("SEED_DEMO_DATA", "false").lower() == "true":
            if db.scalar(select(Product.id).limit(1)) is None:
                for sku, name, desc, price, stock in DEMO_PRODUCTS:
                    db.add(Product(sku=sku, name=name, description=desc, price=Decimal(price), stock=stock))
                db.commit()
                log.info("demo products created")


if __name__ == "__main__":
    main()
