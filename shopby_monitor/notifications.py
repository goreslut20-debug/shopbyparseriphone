"""Pure notification decisions, independent from HTML, HTTP and persistence."""

from decimal import Decimal

from .config import PRODUCT_NAME, PRODUCT_URL


def should_notify(
    current_minimum_price: Decimal,
    previous_observed_price: Decimal | None,
    notification_threshold: Decimal,
) -> bool:
    return current_minimum_price < notification_threshold and (
        previous_observed_price is None or current_minimum_price < previous_observed_price
    )


def notification_text(current_minimum_price: Decimal, *, scheduled_test: bool = False) -> str:
    heading = "Тестовая проверка реальной цены." if scheduled_test else "Цена снизилась ниже порога!"
    return (
        f"{heading}\n{PRODUCT_NAME}\n"
        f"Текущая минимальная цена: {current_minimum_price:.2f} BYN\n{PRODUCT_URL}"
    )
