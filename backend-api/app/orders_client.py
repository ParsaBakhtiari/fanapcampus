"""HTTP client for the internal Order Service."""
import httpx
from fastapi import HTTPException

from .config import get_settings


def _client() -> httpx.Client:
    s = get_settings()
    return httpx.Client(
        base_url=s.order_service_url,
        headers={"X-Internal-Token": s.internal_token},
        timeout=s.order_timeout_seconds,
    )


def call(method: str, path: str, **kwargs) -> httpx.Response:
    try:
        with _client() as client:
            response = client.request(method, path, **kwargs)
    except httpx.RequestError as exc:
        raise HTTPException(503, "order service unavailable") from exc
    if response.status_code == 404:
        raise HTTPException(404, "order not found")
    if response.status_code >= 500:
        raise HTTPException(502, "order service error")
    if response.is_error:
        raise HTTPException(response.status_code, response.json().get("detail", "order service rejected request"))
    return response


def ping() -> bool:
    try:
        with _client() as client:
            return client.get("/healthz/live", timeout=2).status_code == 200
    except httpx.RequestError:
        return False
