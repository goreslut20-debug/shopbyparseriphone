import contextlib
import io
import json
import unittest
import urllib.error
from unittest.mock import MagicMock, patch

from shopby_monitor.config import MonitorError, PRODUCT_URL
from shopby_monitor.main import main
from shopby_monitor.network import fetch_product_page, send_telegram_message


class TelegramTests(unittest.TestCase):
    def response_context(self, payload):
        response = MagicMock()
        response.__enter__.return_value.read.return_value = json.dumps(payload).encode()
        return response

    @patch.dict("os.environ", {"TELEGRAM_BOT_TOKEN": "private-token", "TELEGRAM_CHAT_ID": "private-chat"})
    def test_success_and_json_request(self):
        with patch("shopby_monitor.network.urllib.request.build_opener") as opener:
            opener.return_value.open.return_value = self.response_context(
                {"ok": True, "result": {"message_id": 42}}
            )
            self.assertEqual(send_telegram_message("hello"), 42)
            request = opener.return_value.open.call_args.args[0]
            self.assertEqual(request.get_method(), "POST")
            self.assertEqual(json.loads(request.data)["text"], "hello")

    @patch.dict("os.environ", {"TELEGRAM_BOT_TOKEN": "private-token", "TELEGRAM_CHAT_ID": "private-chat"})
    def test_rejects_unconfirmed_sends(self):
        for payload in ({"ok": False, "description": "private-token private-chat"},
                        {"ok": True}, {"ok": True, "result": {"message_id": True}}, []):
            with self.subTest(payload=payload), patch(
                "shopby_monitor.network.urllib.request.build_opener"
            ) as opener:
                opener.return_value.open.return_value = self.response_context(payload)
                with self.assertRaises(MonitorError) as raised:
                    send_telegram_message("hello")
                self.assertNotIn("private-token", str(raised.exception))
                self.assertNotIn("private-chat", str(raised.exception))

    @patch.dict("os.environ", {"TELEGRAM_BOT_TOKEN": "private-token", "TELEGRAM_CHAT_ID": "private-chat"})
    def test_timeout_is_not_retried_or_exposed(self):
        with patch("shopby_monitor.network.urllib.request.build_opener") as opener:
            opener.return_value.open.side_effect = urllib.error.URLError("private-token private-chat")
            with self.assertRaises(MonitorError) as raised:
                send_telegram_message("hello")
            self.assertNotIn("private-token", str(raised.exception))
            self.assertNotIn("private-chat", str(raised.exception))
            self.assertEqual(opener.return_value.open.call_count, 1)

    @patch.dict("os.environ", {}, clear=True)
    def test_missing_secrets_fail_without_request(self):
        with patch("shopby_monitor.network.urllib.request.build_opener") as opener:
            with self.assertRaisesRegex(MonitorError, "Secrets"):
                send_telegram_message("hello")
            opener.assert_not_called()

    @patch.dict("os.environ", {"TELEGRAM_BOT_TOKEN": "private-token", "TELEGRAM_CHAT_ID": "private-chat"})
    def test_telegram_refusal_explains_action_without_exposing_response(self):
        descriptions = (
            ("Forbidden: bot was blocked by the user private-token", "Разблокируйте"),
            ("Forbidden: bots can't send messages to bots private-chat", "ID другого бота"),
            ("private-token private-chat", "Проверьте Secrets"),
        )
        for description, expected in descriptions:
            with self.subTest(description=description), patch(
                "shopby_monitor.network.urllib.request.build_opener"
            ) as opener:
                body = io.BytesIO(json.dumps({"ok": False, "description": description}).encode())
                opener.return_value.open.side_effect = urllib.error.HTTPError(
                    "https://api.telegram.org/botprivate-token/sendMessage", 403, "Forbidden", {}, body
                )
                with self.assertRaises(MonitorError) as raised:
                    send_telegram_message("ordinary alert")
                self.assertIn(expected, str(raised.exception))
                self.assertNotIn("private-token", str(raised.exception))
                self.assertNotIn("private-chat", str(raised.exception))

    def test_unexpected_exception_does_not_leak_secrets_in_cli(self):
        stderr = io.StringIO()
        with (
            patch("sys.argv", ["monitor", "--test-telegram"]),
            patch("shopby_monitor.main.send_telegram_message", side_effect=ValueError("private-token")),
            contextlib.redirect_stderr(stderr),
        ):
            self.assertEqual(main(), 1)
        self.assertNotIn("private-token", stderr.getvalue())


class ShopFetchTests(unittest.TestCase):
    def test_temporary_errors_retry_with_bounded_attempts(self):
        with (
            patch("shopby_monitor.network.urllib.request.urlopen", side_effect=urllib.error.URLError("down")) as fetch,
            patch("shopby_monitor.network.time.sleep"),
        ):
            with self.assertRaises(MonitorError):
                fetch_product_page()
            self.assertEqual(fetch.call_count, 3)

    def test_403_is_not_retried_or_parsed_as_product(self):
        error = urllib.error.HTTPError(PRODUCT_URL, 403, "Forbidden", {}, None)
        with patch("shopby_monitor.network.urllib.request.urlopen", side_effect=error) as fetch:
            with self.assertRaisesRegex(MonitorError, "HTTP 403"):
                fetch_product_page()
            self.assertEqual(fetch.call_count, 1)

    def test_other_page_redirect_is_rejected(self):
        response = MagicMock()
        response.__enter__.return_value.url = "https://shop.by/captcha/"
        with patch("shopby_monitor.network.urllib.request.urlopen", return_value=response):
            with self.assertRaises(MonitorError):
                fetch_product_page()
