from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    service_name: str = "order-service"
    environment: str = "production"
    log_level: str = "INFO"

    # RDS MySQL — database owned by this service (order_db)
    database_url: str = "mysql+pymysql://orders:orders@mysql:3306/order_db"
    db_pool_size: int = 5
    db_max_overflow: int = 10

    # Only Backend API may call this service.
    internal_token: str = Field(min_length=32)

    # OBS (S3-compatible API)
    obs_endpoint: str | None = None
    obs_region: str = "region-1"
    obs_access_key: str | None = None
    obs_secret_key: str | None = None
    obs_bucket_invoices: str = "ecommerce-invoices"
    obs_addressing_style: str = "virtual"
    obs_sse: str = ""

    shop_name: str = "Pardis Shop"


@lru_cache
def get_settings() -> Settings:
    return Settings()
