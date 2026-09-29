from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field


class ItemIn(BaseModel):
    product_id: str = Field(max_length=36)
    sku: str = Field(max_length=64)
    name: str = Field(max_length=180)
    unit_price: Decimal = Field(gt=0, max_digits=12, decimal_places=2)
    quantity: int = Field(ge=1, le=99)


class OrderCreate(BaseModel):
    user_id: str = Field(max_length=36)
    user_email: str = Field(default="", max_length=255)
    idempotency_key: str = Field(min_length=8, max_length=64)
    currency: str = Field(min_length=3, max_length=3)
    shipping_address: str = Field(min_length=5, max_length=500)
    items: list[ItemIn] = Field(min_length=1, max_length=100)


class ItemOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    product_id: str
    sku: str
    name: str
    unit_price: Decimal
    quantity: int


class OrderOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    number: str
    user_id: str
    user_email: str
    status: str
    currency: str
    total: Decimal
    shipping_address: str
    has_invoice: bool
    created_at: datetime
    items: list[ItemOut]
    duplicate: bool = False
