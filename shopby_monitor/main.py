"""Run one check; GitHub Actions provides the schedule and remote persistence."""

import argparse
import sys
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

from .config import DEFAULT_STATE_PATH, MonitorError, notification_threshold, parse_money
from .network import fetch_product_page, send_telegram_message, telegram_credentials
from .notifications import notification_text, should_notify
from .parser import extract_minimum_price
from .state import load_state, save_state


def process_price(
    current_price: Decimal, state_path: Path, threshold: Decimal,
    *, notify_current_price: bool = False,
) -> bool:
    previous_state = load_state(state_path)
    previous_price = (
        parse_money(previous_state["last_observed_price"])
        if previous_state["last_observed_price"] is not None else None
    )
    notification_required = should_notify(current_price, previous_price, threshold) or (
        notify_current_price and current_price < threshold
    )
    next_state = previous_state.copy()
    observed_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    if notification_required:
        # Updating notification state before this confirmation would lose alerts.
        message_id = send_telegram_message(
            notification_text(current_price, scheduled_test=notify_current_price)
        )
        next_state.update(
            last_notified_price=f"{current_price:.2f}",
            last_notified_at=observed_at,
            last_telegram_message_id=message_id,
        )
        print(f"Telegram подтвердил отправку. Цена: {current_price:.2f} BYN; message_id={message_id}.")
    else:
        print("Уведомление не требуется: цена не ниже порога либо не снизилась.")
    # Rises MUST also be persisted to detect a later fall below the threshold.
    # Leave unchanged prices byte-for-byte unchanged to avoid hourly empty commits.
    if current_price != previous_price:
        next_state.update(last_observed_price=f"{current_price:.2f}", last_observed_at=observed_at)
    if next_state != previous_state:
        save_state(state_path, next_state)
        print("JSON состояния сохранён локально; workflow должен отправить его в GitHub.")
    else:
        print("Состояние не изменилось.")
    return notification_required


def main() -> int:
    argument_parser = argparse.ArgumentParser(description="Проверка цены телевизора на Shop.by")
    argument_parser.add_argument("--state", type=Path, default=Path(DEFAULT_STATE_PATH))
    mode = argument_parser.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true", help="Без Telegram и изменения состояния")
    mode.add_argument("--test-telegram", action="store_true", help="Тест Telegram без проверки цены")
    mode.add_argument(
        "--notify-current-price", action="store_true",
        help="Тест с реальной ценой: уведомить ниже порога даже без снижения",
    )
    arguments = argument_parser.parse_args()
    try:
        if arguments.test_telegram:
            send_telegram_message(
                "Тест Shop.by-монитора: Telegram работает. "
                "Это проверка связи, а не уведомление о снижении цены."
            )
            print("Telegram подтвердил отправку тестового сообщения. Состояние не изменено.")
            return 0
        threshold = notification_threshold()
        load_state(arguments.state)  # Fail before network activity on corrupted state.
        if not arguments.dry_run:
            telegram_credentials()  # Detect missing Secrets even while the price is high.
        current_price = extract_minimum_price(fetch_product_page())
        print(f"Нужный товар подтверждён. Минимальная цена: {current_price:.2f} BYN; порог: {threshold:.2f} BYN.")
        if arguments.dry_run:
            print("Пробный запуск: Telegram не вызывается, JSON состояния не изменяется.")
        else:
            process_price(
                current_price, arguments.state, threshold,
                notify_current_price=arguments.notify_current_price,
            )
        return 0
    except MonitorError as error:
        print(f"Ошибка: {error}", file=sys.stderr)
        return 1
    except Exception as error:
        # Raw exceptions can include Telegram's token-bearing URL. No traceback.
        print(f"Непредвиденная ошибка ({type(error).__name__}); проверьте код и состояние.", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
