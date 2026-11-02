import contextlib
import io
import json
import tempfile
import unittest
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch

from shopby_monitor.config import MonitorError
from shopby_monitor.main import main, process_price
from shopby_monitor.state import initial_state, load_state, save_state


class StateIntegrationTests(unittest.TestCase):
    def setUp(self):
        temporary_directory = tempfile.TemporaryDirectory()
        self.addCleanup(temporary_directory.cleanup)
        self.state_path = Path(temporary_directory.name) / "price_state.json"
        save_state(self.state_path, initial_state())
        self.stdout = contextlib.redirect_stdout(io.StringIO())
        self.stdout.__enter__()
        self.addCleanup(self.stdout.__exit__, None, None, None)

    def test_send_is_confirmed_before_state_is_saved(self):
        def confirmed_send(message):
            self.assertIsNone(load_state(self.state_path)["last_notified_price"])
            return 42

        with patch("shopby_monitor.main.send_telegram_message", side_effect=confirmed_send):
            self.assertTrue(process_price(Decimal("2990"), self.state_path, Decimal("3000")))
        state = load_state(self.state_path)
        self.assertEqual(state["last_notified_price"], "2990.00")
        self.assertEqual(state["last_telegram_message_id"], 42)

    def test_failed_send_leaves_state_unchanged_and_next_run_retries(self):
        original_bytes = self.state_path.read_bytes()
        with patch("shopby_monitor.main.send_telegram_message", side_effect=MonitorError("failed")):
            with self.assertRaises(MonitorError):
                process_price(Decimal("2990"), self.state_path, Decimal("3000"))
        self.assertEqual(self.state_path.read_bytes(), original_bytes)
        with patch("shopby_monitor.main.send_telegram_message", return_value=42) as send:
            process_price(Decimal("2990"), self.state_path, Decimal("3000"))
            send.assert_called_once()

    def test_restart_preserves_silence_and_rise_rearms(self):
        with patch("shopby_monitor.main.send_telegram_message", return_value=42) as send:
            for amount in ("2990", "2990", "3000", "2995"):
                process_price(Decimal(amount), self.state_path, Decimal("3000"))
            self.assertEqual(send.call_count, 2)
        self.assertEqual(load_state(self.state_path)["last_observed_price"], "2995.00")

    def test_unchanged_price_does_not_rewrite_state(self):
        with patch("shopby_monitor.main.send_telegram_message") as send:
            process_price(Decimal("3400"), self.state_path, Decimal("3000"))
            original_bytes = self.state_path.read_bytes()
            process_price(Decimal("3400"), self.state_path, Decimal("3000"))
            self.assertEqual(self.state_path.read_bytes(), original_bytes)
            send.assert_not_called()

    def test_scheduled_price_test_notifies_for_unchanged_price_below_temporary_threshold(self):
        with patch("shopby_monitor.main.send_telegram_message", return_value=42) as send:
            process_price(Decimal("3400"), self.state_path, Decimal("3000"))
            process_price(Decimal("3400"), self.state_path, Decimal("3450"), notify_current_price=True)
            send.assert_called_once()
            self.assertIn("Тестовая проверка реальной цены", send.call_args.args[0])
            self.assertIn("3400.00 BYN", send.call_args.args[0])
        self.assertEqual(load_state(self.state_path)["last_notified_price"], "3400.00")

    def test_scheduled_price_test_still_respects_strict_threshold(self):
        with patch("shopby_monitor.main.send_telegram_message") as send:
            process_price(Decimal("3450"), self.state_path, Decimal("3450"), notify_current_price=True)
            send.assert_not_called()

    def test_corrupt_or_missing_state_never_silently_resets(self):
        self.state_path.write_text("broken", encoding="utf-8")
        with self.assertRaises(MonitorError):
            load_state(self.state_path)
        with self.assertRaises(MonitorError):
            load_state(self.state_path.parent / "missing.json")

    def test_invalid_state_fields_are_rejected(self):
        for field, value in (("product_id", "wrong"), ("last_observed_price", 100),
                             ("last_observed_price", "NaN"), ("schema_version", True),
                             ("last_telegram_message_id", -1), ("last_notified_price", "2990")):
            with self.subTest(field=field, value=value):
                state = initial_state()
                state[field] = value
                self.state_path.write_text(json.dumps(state), encoding="utf-8")
                with self.assertRaises(MonitorError):
                    load_state(self.state_path)

    def test_atomic_replace_failure_preserves_previous_json(self):
        original_bytes = self.state_path.read_bytes()
        with patch("shopby_monitor.state.os.replace", side_effect=OSError("disk failure")):
            with self.assertRaises(MonitorError):
                save_state(self.state_path, initial_state())
        self.assertEqual(self.state_path.read_bytes(), original_bytes)
        self.assertEqual(list(self.state_path.parent.glob("*.tmp")), [])

    def test_parse_failure_sends_nothing_and_preserves_state(self):
        original_bytes = self.state_path.read_bytes()
        with (
            patch("sys.argv", ["monitor", "--state", str(self.state_path)]),
            patch("shopby_monitor.main.telegram_credentials", return_value=("test", "test")),
            patch("shopby_monitor.main.fetch_product_page", return_value="<html>blocked</html>"),
            patch("shopby_monitor.main.send_telegram_message") as send,
            contextlib.redirect_stderr(io.StringIO()),
        ):
            self.assertEqual(main(), 1)
            send.assert_not_called()
        self.assertEqual(self.state_path.read_bytes(), original_bytes)

    def test_dry_run_does_not_require_secrets_or_modify_state(self):
        fixture = Path(__file__).parent / "fixtures" / "shopby_product_excerpt.html"
        original_bytes = self.state_path.read_bytes()
        with (
            patch("sys.argv", ["monitor", "--dry-run", "--state", str(self.state_path)]),
            patch("shopby_monitor.main.fetch_product_page", return_value=fixture.read_text()),
            patch("shopby_monitor.main.telegram_credentials") as credentials,
            patch("shopby_monitor.main.send_telegram_message") as send,
        ):
            self.assertEqual(main(), 0)
            credentials.assert_not_called()
            send.assert_not_called()
        self.assertEqual(self.state_path.read_bytes(), original_bytes)

    def test_explicit_telegram_test_does_not_fetch_price_or_modify_state(self):
        original_bytes = self.state_path.read_bytes()
        with (
            patch("sys.argv", ["monitor", "--test-telegram", "--state", str(self.state_path)]),
            patch("shopby_monitor.main.fetch_product_page") as fetch,
            patch("shopby_monitor.main.send_telegram_message", return_value=42) as send,
        ):
            self.assertEqual(main(), 0)
            fetch.assert_not_called()
            send.assert_called_once()
        self.assertEqual(self.state_path.read_bytes(), original_bytes)
