"""Runtime settings. Every value comes from environment variables
(Kubernetes ConfigMap + Secret, or docker-compose environment)."""
from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    service_name: str = "backend-api"
    environment: str = "production"
    log_level: str = "INFO"

    # RDS MySQL — database owned by this service (shop_db)
    database_url: str = "mysql+pymysql://shop:shop@mysql:3306/shop_db"
    db_pool_size: int = 5
    db_max_overflow: int = 10

    # DCS Redis
    redis_url: str = "redis://redis:6379/0"
    cart_ttl_seconds: int = 7 * 24 * 3600
    catalog_cache_seconds: int = 60
    login_attempts_per_minute: int = 10

    # Auth
    jwt_secret: str = Field(min_length=32)
    jwt_ttl_minutes: int = 60

    # Order Service (internal, ClusterIP only)
    order_service_url: str = "http://order-service:8000"
    internal_token: str = Field(min_length=32)
    order_timeout_seconds: float = 10.0

    # OBS (S3-compatible API)
    obs_endpoint: str | None = None
    obs_region: str = "region-1"
    obs_access_key: str | None = None
    obs_secret_key: str | None = None
    obs_bucket_images: str = "ecommerce-images"
    obs_addressing_style: str = "virtual"  # "virtual" for OBS, "path" for MinIO
    obs_sse: str = ""  # "" = no header, or "AES256"
    max_image_bytes: int = 5 * 1024 * 1024

    currency: str = "USD"


@lru_cache
def get_settings() -> Settings:
    return Settings()
