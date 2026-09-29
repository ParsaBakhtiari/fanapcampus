"""One-shot schema job for order_db. Safe to run many times."""
import sys
import time

from sqlalchemy import text

from . import models  # noqa: F401  (registers tables)
from .db import Base, engine
from .logs import setup_logging

log = setup_logging("order-service-migrate", "INFO")


def main() -> None:
    for attempt in range(1, 31):
        try:
            with engine.connect() as conn:
                conn.execute(text("SELECT 1"))
            break
        except Exception as exc:
            log.warning(f"database not ready (attempt {attempt}/30): {exc}")
            time.sleep(2)
    else:
        log.error("database never became ready")
        sys.exit(1)
    Base.metadata.create_all(engine)
    log.info("schema ready")


if __name__ == "__main__":
    main()
