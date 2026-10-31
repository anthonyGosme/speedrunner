"""Prices, in euros."""

PRICES: dict[str, float] = {"book": 12.5, "pen": 1.2, "bag": 30.0}


def price_of(sku: str) -> float:
    return PRICES[sku]
