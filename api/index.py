import asyncio
import json
import logging
import os
from http.server import BaseHTTPRequestHandler
from urllib.error import HTTPError
from urllib.parse import parse_qs, urlparse
from urllib.request import Request, urlopen

from aiogram import Bot, Dispatcher, F, Router
from aiogram.types import Message, ReplyParameters, Update


logging.basicConfig(level=logging.INFO)

BOT_TOKEN = os.getenv("BOT_TOKEN", "")
WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET", "")


async def capture_sticker_file_id(message: Message):
    """
    Если отправить боту стикер в личку, он пришлёт его file_id.
    """
    await message.answer(
        "Возьми этот file_id и вставь его в переменную окружения "
        "STICKER_FILE_ID:\n\n"
        f"<code>{message.sticker.file_id}</code>",
        parse_mode="HTML",
    )


async def reply_with_sticker(message: Message):
    """
    Отвечает стикером на любое сообщение человека в группе/супергруппе.
    """
    sticker_id = os.getenv("STICKER_FILE_ID", "")

    if not sticker_id:
        logging.warning("STICKER_FILE_ID пустой. Бот не знает, какой стикер отправлять.")
        return

    await message.bot.send_sticker(
        chat_id=message.chat.id,
        sticker=sticker_id,
        message_thread_id=message.message_thread_id,
        reply_parameters=ReplyParameters(
            message_id=message.message_id,
            allow_sending_without_reply=True,
        ),
    )


def create_router() -> Router:
    router = Router(name="sticker_reply")

    router.message.register(
        capture_sticker_file_id,
        F.chat.type == "private",
        F.sticker,
    )

    router.message.register(
        reply_with_sticker,
        F.chat.type.in_({"group", "supergroup"}),
        lambda message: message.from_user is not None and not message.from_user.is_bot,
    )

    return router


async def process_update(raw_body: bytes):
    bot = Bot(token=BOT_TOKEN)
    dp = Dispatcher()
    dp.include_router(create_router())

    try:
        update = Update.model_validate_json(raw_body)
        await dp.feed_update(bot=bot, update=update)
    finally:
        await bot.session.close()


def respond(handler_obj, status: int, text: str, content_type: str = "text/plain; charset=utf-8"):
    payload = text.encode("utf-8")

    handler_obj.send_response(status)
    handler_obj.send_header("Content-Type", content_type)
    handler_obj.send_header("Content-Length", str(len(payload)))
    handler_obj.end_headers()
    handler_obj.wfile.write(payload)


def json_response(handler_obj, status: int, data: dict):
    respond(
        handler_obj,
        status,
        json.dumps(data, ensure_ascii=False, indent=2),
        "application/json; charset=utf-8",
    )


def get_base_url(handler_obj) -> str:
    base = os.getenv("WEBHOOK_URL", "").strip()
    if base:
        return base.rstrip("/")

    proto = handler_obj.headers.get("x-forwarded-proto", "https").split(",")[0].strip() or "https"
    host = handler_obj.headers.get("host", "").strip()

    if host:
        return f"{proto}://{host}"

    vercel_url = os.getenv("VERCEL_URL", "").strip()
    if vercel_url:
        if vercel_url.startswith(("http://", "https://")):
            return vercel_url.rstrip("/")
        return f"https://{vercel_url}"

    return ""


def set_webhook(base_url: str, webhook_path: str):
    webhook_url = base_url.rstrip("/") + webhook_path

    payload = {
        "url": webhook_url,
        "allowed_updates": ["message"],
    }

    if WEBHOOK_SECRET:
        payload["secret_token"] = WEBHOOK_SECRET

    data = json.dumps(payload).encode("utf-8")

    request = Request(
        f"https://api.telegram.org/bot{BOT_TOKEN}/setWebhook",
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )

    try:
        with urlopen(request, timeout=10) as response:
            return webhook_url, response.status, response.read().decode("utf-8")
    except HTTPError as e:
        return webhook_url, e.code, e.read().decode("utf-8")


class handler(BaseHTTPRequestHandler):
    def do_GET(self):
        parsed = urlparse(self.path)
        query = parse_qs(parsed.query)

        setup = query.get("setup", ["0"])[0].lower()
        secret = query.get("secret", [""])[0] or self.headers.get("x-set-webhook-secret", "")
        webhook_path = os.getenv("WEBHOOK_PATH") or parsed.path or "/"

        if setup in {"1", "true", "yes"}:
            if WEBHOOK_SECRET and secret != WEBHOOK_SECRET:
                return json_response(
                    self,
                    403,
                    {
                        "ok": False,
                        "error": "Нужен секрет. Добавь ?secret=ТВОЙ_WEBHOOK_SECRET",
                    },
                )

            if not BOT_TOKEN:
                return json_response(
                    self,
                    500,
                    {"ok": False, "error": "BOT_TOKEN не задан в Vercel."},
                )

            base_url = get_base_url(self)
            if not base_url:
                return json_response(
                    self,
                    400,
                    {"ok": False, "error": "Не удалось определить базовый URL."},
                )

            try:
                webhook_url, status, text = set_webhook(base_url, webhook_path)
                return json_response(
                    self,
                    status,
                    {
                        "webhook_url": webhook_url,
                        "telegram_status": status,
                        "telegram_response": text,
                    },
                )
            except Exception as e:
                return json_response(
                    self,
                    500,
                    {"ok": False, "error": str(e)},
                )

        return json_response(
            self,
            200,
            {
                "ok": True,
                "service": "Telegram sticker webhook",
                "webhook_path": webhook_path,
                "setup_path": f"{webhook_path}?setup=1"
                + ("&secret=YOUR_WEBHOOK_SECRET" if WEBHOOK_SECRET else ""),
                "has_bot_token": bool(BOT_TOKEN),
                "has_sticker_file_id": bool(os.getenv("STICKER_FILE_ID", "")),
                "secret_required": bool(WEBHOOK_SECRET),
            },
        )

    def do_POST(self):
        if WEBHOOK_SECRET:
            secret = self.headers.get("X-Telegram-Bot-Api-Secret-Token", "")
            if secret != WEBHOOK_SECRET:
                return json_response(
                    self,
                    403,
                    {"ok": False, "error": "Bad secret token"},
                )

        if not BOT_TOKEN:
            logging.error("BOT_TOKEN missing")
            return json_response(self, 200, {"ok": False, "error": "missing BOT_TOKEN"})

        content_length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(content_length) if content_length > 0 else b""

        if not body:
            return json_response(self, 200, {"ok": False, "error": "empty body"})

        try:
            asyncio.run(process_update(body))
        except Exception:
            logging.exception("Failed to process Telegram update")

        return json_response(self, 200, {"ok": True})

    def log_message(self, format, *args):
        pass
