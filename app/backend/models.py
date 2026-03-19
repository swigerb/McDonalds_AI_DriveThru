from pydantic import BaseModel

__all__ = ["OrderItem", "OrderSummary"]


class OrderItem(BaseModel):
    item: str
    size: str
    quantity: int
    price: float
    display: str


class OrderSummary(BaseModel):
    items: list[OrderItem]
    total: float
    tax: float
    finalTotal: float