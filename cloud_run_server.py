"""
cloud_run_server.py — Minimal HTTP Server for Cloud Run
========================================================
Cloud Run needs an HTTP server to stay alive.
Cloud Scheduler hits POST /run  → bot executes → container shuts down.

This keeps the container lightweight:
  - GET  /       → health check (200 OK)
  - POST /run    → triggers the bot (used by Cloud Scheduler)
"""

import os
import threading
import logging
from http.server import BaseHTTPRequestHandler, HTTPServer
from naukri_bot import run_bot

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)

# Track if a run is already in progress (prevent overlapping runs)
_running = False
_lock    = threading.Lock()


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
        """Trigger endpoint — Cloud Scheduler calls POST /run every hour."""
        global _running

        if self.path != "/run":
            self._respond(404, "Not Found")
            return

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
    logger.info("  GET  /     -> health check")
    logger.info("  POST /run  -> trigger bot run")
    server.serve_forever()
