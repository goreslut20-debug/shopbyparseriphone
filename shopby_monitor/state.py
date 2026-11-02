"""Validated, atomically replaced JSON state. No credentials or chat identifiers."""

import json
import os
import tempfile
from pathlib import Path

from .config import MonitorError, PRODUCT_ID, PRODUCT_URL, parse_money


def initial_state() -> dict:
    return {
        "schema_version": 1,
        "product_id": PRODUCT_ID,
        "product_url": PRODUCT_URL,
        "last_observed_price": None,
        "last_observed_at": None,
        "last_notified_price": None,
        "last_notified_at": None,
        "last_telegram_message_id": None,
    }


def load_state(state_path: Path) -> dict:
    try:
        state = json.loads(state_path.read_text(encoding="utf-8"))
    except (OSError, ValueError, UnicodeError):
        raise MonitorError(
            "Не удалось прочитать JSON состояния. Восстановите его из истории Git; "
            "автоматический сброс запрещён во избежание повторных уведомлений."
        ) from None
    expected_fields = initial_state()
    if not isinstance(state, dict) or set(state) != set(expected_fields):
        raise MonitorError("Некорректная структура JSON состояния.")
    if (
        type(state["schema_version"]) is not int or state["schema_version"] != 1
        or state["product_id"] != PRODUCT_ID or state["product_url"] != PRODUCT_URL
    ):
        raise MonitorError("JSON состояния относится к другому товару или версии.")
    for field_name in ("last_observed_price", "last_notified_price"):
        if state[field_name] is not None:
            parse_money(state[field_name])
    for field_name in ("last_observed_at", "last_notified_at"):
        if state[field_name] is not None and not isinstance(state[field_name], str):
            raise MonitorError("Некорректная дата в JSON состояния.")
    message_id = state["last_telegram_message_id"]
    if message_id is not None and (type(message_id) is not int or message_id <= 0):
        raise MonitorError("Некорректный message_id в JSON состояния.")
    if (state["last_observed_price"] is None) != (state["last_observed_at"] is None):
        raise MonitorError("Несогласованное состояние предыдущей проверки.")
    notification_fields = [state[name] is not None for name in
                           ("last_notified_price", "last_notified_at", "last_telegram_message_id")]
    if any(notification_fields) and (not all(notification_fields) or state["last_observed_price"] is None):
        raise MonitorError("Несогласованное состояние отправленного уведомления.")
    return state


def save_state(state_path: Path, state: dict) -> None:
    temporary_path = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=state_path.parent,
            prefix="price_state.", suffix=".tmp", delete=False,
        ) as temporary_file:
            temporary_path = Path(temporary_file.name)
            json.dump(state, temporary_file, ensure_ascii=False, indent=2)
            temporary_file.write("\n")
            temporary_file.flush()
            os.fsync(temporary_file.fileno())
        os.replace(temporary_path, state_path)
    except OSError:
        raise MonitorError(
            "Не удалось сохранить JSON состояния. Если сообщение уже отправлено, "
            "восстановите состояние перед повторным запуском."
        ) from None
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)
