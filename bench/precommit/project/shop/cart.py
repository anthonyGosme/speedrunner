"""A shopping cart."""

from dataclasses import dataclass, field

from shop.pricing import price_of


@dataclass
class Cart:
    """Items and their quantities."""

    items: dict[str, int] = field(default_factory=dict)

    def add(self, sku: str, quantity: int = 1) -> None:
        self.items[sku] = self.items.get(sku, 0) + quantity

    def total(self) -> float:
        return sum(price_of(sku) * quantity for sku, quantity in self.items.items())
