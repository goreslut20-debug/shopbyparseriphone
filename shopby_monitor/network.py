"""Network operations with bounded reads and secret-free error messages."""

import json
import os
import time
import urllib.error
import urllib.request

from .config import MonitorError, PRODUCT_URL, USER_AGENT
from .parser import same_product_url


MAX_PAGE_BYTES = 8 * 1024 * 1024


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, response, code, message, headers, new_url):
        return None


def fetch_product_page() -> str:
    request = urllib.request.Request(
        PRODUCT_URL,
        headers={"User-Agent": USER_AGENT, "Accept": "text/html", "Accept-Language": "ru"},
    )
    for attempt in range(3):
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                if not same_product_url(response.url):
                    raise MonitorError("Shop.by перенаправил запрос на другую страницу.")
                if response.headers.get_content_type() != "text/html":
                    raise MonitorError("Shop.by вернул не HTML-страницу.")
                page_bytes = response.read(MAX_PAGE_BYTES + 1)
                if len(page_bytes) > MAX_PAGE_BYTES:
                    raise MonitorError("Ответ Shop.by превышает допустимый размер.")
                return page_bytes.decode(response.headers.get_content_charset() or "utf-8")
        except urllib.error.HTTPError as error:
            status_code = error.code
            error.close()
            if status_code not in {429, 500, 502, 503, 504} or attempt == 2:
                raise MonitorError(f"Shop.by вернул HTTP {status_code}; цена не обновлена.") from None
        except (urllib.error.URLError, TimeoutError, OSError):
            if attempt == 2:
                raise MonitorError("Не удалось загрузить Shop.by после трёх попыток.") from None
        except (UnicodeError, LookupError):
            raise MonitorError("Не удалось прочитать кодировку страницы Shop.by.") from None
        time.sleep(2 ** attempt)
    raise MonitorError("Не удалось загрузить страницу Shop.by.")


def telegram_credentials() -> tuple[str, str]:
    bot_token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    chat_id = os.environ.get("TELEGRAM_CHAT_ID", "").strip()
    if not bot_token or not chat_id:
        raise MonitorError(
            "Добавьте TELEGRAM_BOT_TOKEN и TELEGRAM_CHAT_ID в GitHub Actions Secrets."
        )
    return bot_token, chat_id


def send_telegram_message(message_text: str) -> int:
    bot_token, chat_id = telegram_credentials()
    request = urllib.request.Request(
        f"https://api.telegram.org/bot{bot_token}/sendMessage",
        data=json.dumps({"chat_id": chat_id, "text": message_text,
                         "link_preview_options": {"is_disabled": True}}).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    # Do not retry ambiguous sends: Telegram may already have delivered the message.
    # Never print the request URL, raw exception or response (they can contain secrets).
    try:
        with urllib.request.build_opener(NoRedirect()).open(request, timeout=30) as response:
            response_bytes = response.read(65537)
        if len(response_bytes) > 65536:
            raise MonitorError("Слишком большой ответ Telegram; состояние не обновлено.")
        payload = json.loads(response_bytes)
    except urllib.error.HTTPError as error:
        status_code = error.code
        error.close()
        raise MonitorError(
            f"Telegram вернул HTTP {status_code}. Проверьте Secrets и доступ бота к чату."
        ) from None
    except (urllib.error.URLError, TimeoutError, OSError):
        raise MonitorError(
            "Сетевая ошибка Telegram: доставка не подтверждена, состояние не обновлено."
        ) from None
    except (ValueError, UnicodeError):
        raise MonitorError("Некорректный ответ Telegram; состояние не обновлено.") from None
    if not isinstance(payload, dict) or payload.get("ok") is not True:
        raise MonitorError("Telegram не подтвердил отправку; состояние не обновлено.")
    result = payload.get("result")
    message_id = result.get("message_id") if isinstance(result, dict) else None
    if type(message_id) is not int or message_id <= 0:
        raise MonitorError("Telegram не вернул message_id; состояние не обновлено.")
    return message_id
