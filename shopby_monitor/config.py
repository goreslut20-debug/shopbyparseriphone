"""Product identity and user-editable settings."""

import os
import re
from decimal import Decimal, InvalidOperation


PRODUCT_NAME = 'Xiaomi TV S Pro Mini LED 75" 2026 L75MB-SRU (международная версия)'
PRODUCT_ID = "1014472"
PRODUCT_URL = (
    "https://shop.by/televizory_zhk/"
    "xiaomi_tv_s_pro_mini_led_75_2026_l75mb_sru_mezhdunarodnaya_versiya/"
)
DEFAULT_NOTIFICATION_THRESHOLD = "3000.00"
DEFAULT_STATE_PATH = "state/price_state.json"
USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)


class MonitorError(Exception):
    """An actionable error whose message is safe to write to public logs."""


def parse_money(value: str) -> Decimal:
    """Read a positive BYN amount without binary floating point or guessing."""
    if not isinstance(value, str):
        raise MonitorError("Цена должна быть строкой с положительным числом.")
    value = value.strip()
    if not re.fullmatch(
        r"(?:[0-9]+|[0-9]{1,3}(?:[ \u00a0\u202f][0-9]{3})+)(?:[.,][0-9]{1,2})?",
        value,
    ):
        raise MonitorError("Неизвестный формат цены или порога.")
    normalized_value = re.sub(r"[ \u00a0\u202f]", "", value).replace(",", ".")
    try:
        amount = Decimal(normalized_value).quantize(Decimal("0.01"))
    except InvalidOperation:
        raise MonitorError("Некорректная цена или порог.") from None
    if amount <= 0:
        raise MonitorError("Цена или порог должны быть больше нуля.")
    return amount


def notification_threshold() -> Decimal:
    return parse_money(os.environ.get("PRICE_THRESHOLD_BYN", DEFAULT_NOTIFICATION_THRESHOLD))
