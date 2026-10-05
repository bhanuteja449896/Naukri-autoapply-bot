"""
cloud_run_server.py — Minimal HTTP Server for Cloud Run
========================================================
Cloud Run needs an HTTP server to stay alive.
Cloud Scheduler hits POST /run  → bot executes → container shuts down.

Endpoints:
  GET  /                  → health check (200 OK)
  POST /run               → triggers the bot (used by Cloud Scheduler)
  POST /telegram/webhook  → Telegram webhook: auto-registers chat_id for broadcasts
"""

import os
import json
import threading
import logging
from http.server import BaseHTTPRequestHandler, HTTPServer
from naukri_bot import run_bot, register_telegram_chat_id, send_telegram_notification

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)

# Track if a run is already in progress (prevent overlapping runs)
_running = False
_lock    = threading.Lock()

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()


def _send_reply(chat_id: str, text: str):
    """Send a direct reply to a specific Telegram chat."""
    if not TELEGRAM_BOT_TOKEN or not chat_id:
        return
    try:
        import urllib.request, urllib.parse
        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
        payload = urllib.parse.urlencode({"chat_id": chat_id, "text": text}).encode()
        req = urllib.request.Request(url, data=payload, method="POST")
        urllib.request.urlopen(req, timeout=10)
    except Exception as e:
        logger.warning(f"Telegram reply failed: {e}")


class BotHandler(BaseHTTPRequestHandler):

    def log_message(self, format, *args):
        # Suppress default HTTP server logging (we use our own)
        pass

    def do_GET(self):
        """Health check endpoint — Cloud Run uses this to verify the container is up."""
        if self.path == "/":
            self._respond(200, "Naukri Bot — OK")
        else:
            self._respond(404, "Not Found")

    def do_POST(self):
        """POST endpoints dispatcher."""
        if self.path == "/run":
            self._handle_run()
        elif self.path in ("/telegram/webhook", "/telegram/register"):
            self._handle_telegram_webhook()
        else:
            self._respond(404, "Not Found")

    def _handle_run(self):
        """Trigger endpoint — Cloud Scheduler calls POST /run every hour."""
        global _running

        with _lock:
            if _running:
                self._respond(429, "Bot already running — try again later")
                return
            _running = True

        logger.info("Cloud Scheduler triggered a bot run.")
        try:
            run_bot()
            self._respond(200, "Bot run complete — applications processed successfully")
        except Exception as e:
            logger.error(f"Bot run failed: {e}")
            self._respond(500, f"Bot run failed: {e}")
        finally:
            with _lock:
                _running = False
            logger.info("Bot run finished.")

            # Shut down container immediately so Cloud Run scales down to 0 instances without waiting 15 minutes
            def _exit_now():
                import time
                time.sleep(1.5)
                logger.info("🛑 Entering sleep mode: Exiting container to scale to 0 instances immediately.")
                os._exit(0)

            threading.Thread(target=_exit_now, daemon=True).start()

    def _handle_telegram_webhook(self):
        """
        Handle incoming Telegram webhook updates.
        Any user who sends a message to the bot is auto-registered as a subscriber.
        They'll receive all future job application notifications.
        """
        try:
            content_length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(content_length)
            update = json.loads(body.decode("utf-8"))

            # Extract message / callback_query
            msg = update.get("message") or update.get("callback_query", {}).get("message")
            if msg and "chat" in msg:
                chat_id = str(msg["chat"]["id"])
                user    = msg["chat"].get("first_name", "Friend")

                # Register this chat_id so they receive future broadcasts
                register_telegram_chat_id(chat_id)

                # Send confirmation reply
                text = msg.get("text", "").strip().lower()
                if text in ("/start", "start", "hello", "hi"):
                    reply = (
                        f"👋 Hi {user}! You've been registered to receive job application updates.\n\n"
                        f"Every time the bot applies to jobs, you'll get a notification here. ✅\n\n"
                        f"Commands:\n"
                        f"  /start   — Register / confirm subscription\n"
                        f"  /status  — Check if bot is active"
                    )
                elif text == "/status":
                    reply = "✅ Bot is active and running. You'll receive notifications after each job search."
                else:
                    reply = f"✅ {user}, you're subscribed to job updates! You'll receive notifications automatically."

                _send_reply(chat_id, reply)
                logger.info(f"📲 Telegram webhook: registered chat_id {chat_id} ({user})")

            self._respond(200, "OK")
        except Exception as e:
            logger.warning(f"Telegram webhook error: {e}")
            self._respond(200, "OK")  # Always return 200 to Telegram

    def _respond(self, code: int, body: str):
        self.send_response(code)
        self.send_header("Content-Type", "text/plain")
        self.end_headers()
        self.wfile.write(body.encode())


if __name__ == "__main__":
    port = int(os.getenv("PORT", "8080"))
    server = HTTPServer(("0.0.0.0", port), BotHandler)
    logger.info(f"Server listening on port {port}")
    logger.info("Endpoints:")
    logger.info("  GET  /                 -> health check")
    logger.info("  POST /run              -> trigger bot run")
    logger.info("  POST /telegram/webhook -> Telegram webhook (auto-register subscribers)")
    server.serve_forever()
