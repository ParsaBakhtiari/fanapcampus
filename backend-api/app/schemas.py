from decimal import Decimal

from pydantic import BaseModel, ConfigDict, EmailStr, Field


class RegisterRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8, max_length=128)
    full_name: str = Field(default="", max_length=120)


class LoginRequest(BaseModel):
    email: str = Field(min_length=3, max_length=255)
    password: str = Field(min_length=1, max_length=128)


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in: int


class UserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    email: str
    full_name: str
    role: str


class ProductOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    sku: str
    name: str
    description: str
    price: Decimal
    stock: int
    has_image: bool
    is_active: bool


class ProductCreate(BaseModel):
    sku: str = Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9._-]+$")
    name: str = Field(min_length=1, max_length=180)
    description: str = Field(default="", max_length=5000)
    price: Decimal = Field(gt=0, max_digits=12, decimal_places=2)
    stock: int = Field(default=0, ge=0)


class ProductUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=180)
    description: str | None = Field(default=None, max_length=5000)
    price: Decimal | None = Field(default=None, gt=0, max_digits=12, decimal_places=2)
    stock: int | None = Field(default=None, ge=0)
    is_active: bool | None = None


class CartItemIn(BaseModel):
    product_id: str = Field(min_length=1, max_length=36)
    quantity: int = Field(ge=1, le=99)


class CartLine(BaseModel):
    product_id: str
    name: str
    unit_price: Decimal
    quantity: int
    line_total: Decimal
    available: bool


class CartOut(BaseModel):
    items: list[CartLine]
    total: Decimal
    currency: str


class CheckoutRequest(BaseModel):
    shipping_address: str = Field(min_length=5, max_length=500)
