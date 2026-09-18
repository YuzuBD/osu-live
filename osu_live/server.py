"""HTTP server: server-sent events for live data, JSON for snapshots.

Stdlib only, so the overlay runs anywhere Python does. Threaded, because each
SSE subscriber holds a connection open for the length of a play session.
"""

from __future__ import annotations

import json
import mimetypes
import socket
import urllib.request
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

from .config import Config
from .state import StateManager

WEB_ROOT = Path(__file__).resolve().parent / "web"

#: Longest a subscriber may go without traffic before we emit a keep-alive.
HEARTBEAT_SECONDS = 15.0


def local_addresses(port: int) -> list[str]:
    """Best-effort LAN addresses a phone on the same network can reach."""
    addresses: list[str] = []
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            address = info[4][0]
            if address not in addresses and _is_lan_address(address):
                addresses.append(address)
    except OSError:
        pass
    # The address used for the default route usually reaches the LAN even when
    # hostname resolution is unhelpful, so prefer it.
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
            probe.connect(("8.8.8.8", 80))
            address = probe.getsockname()[0]
            if _is_lan_address(address):
                if address in addresses:
                    addresses.remove(address)
                addresses.insert(0, address)
    except OSError:
        pass
    return [f"http://{address}:{port}" for address in addresses]


def _is_lan_address(address: str) -> bool:
    """Whether an address is worth handing to a phone.

    Drops loopback, link-local (169.254/16) and the 198.18/15 range that
    proxy TUN adapters such as Clash use -- phones cannot route to those.
    """
    parts = address.split(".")
    if len(parts) != 4:
        return False
    try:
        first, second = int(parts[0]), int(parts[1])
    except ValueError:
        return False
    if first == 127:
        return False
    if (first, second) == (169, 254):
        return False
    if first == 198 and 18 <= second <= 19:
        return False
    return True


class OverlayHandler(BaseHTTPRequestHandler):
    server_version = "osu-live"
    protocol_version = "HTTP/1.1"

    manager: StateManager
    config: Config

    # -- helpers -------------------------------------------------------------

    def log_message(self, fmt: str, *args: Any) -> None:
        """Quiet by default; SSE would otherwise flood the console."""
        if self.server.verbose:  # type: ignore[attr-defined]
            super().log_message(fmt, *args)

    def _authorised(self, query: dict[str, list[str]]) -> bool:
        token = self.config.access_token
        if not token:
            return True
        header = self.headers.get("Authorization", "")
        if header.removeprefix("Bearer ").strip() == token:
            return True
        return token in query.get("token", [])

    def _send_json(self, payload: Any, status: int = HTTPStatus.OK) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

    def _send_error_json(self, status: int, message: str) -> None:
        self._send_json({"error": message}, status)

    # -- routes --------------------------------------------------------------

    def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
        parsed = urlparse(self.path)
        query = parse_qs(parsed.query)
        if not self._authorised(query):
            self._send_error_json(HTTPStatus.UNAUTHORIZED, "missing or bad token")
            return
        path = parsed.path.rstrip("/") or "/"
        try:
            if path == "/api/state":
                self._send_json(self.manager.state())
            elif path == "/api/config":
                self._send_json(
                    {
                        "poll_interval_ms": self.config.poll_interval_ms,
                        "pp_interval_ms": self.config.pp_interval_ms,
                        "bancho": self.config.bancho_api_url,
                        "player": self.config.bancho_player,
                    }
                )
            elif path == "/api/events":
                self._stream(query)
            elif path == "/api/avatar":
                self._send_avatar(query)
            elif path == "/api/background":
                self._send_background()
            elif path == "/" or not path.startswith("/api"):
                self._serve_static("/index.html" if path == "/" else path)
            else:
                self._send_error_json(HTTPStatus.NOT_FOUND, "unknown endpoint")
        except (BrokenPipeError, ConnectionAbortedError, ConnectionResetError):
            pass  # client went away mid-response

    def _serve_static(self, path: str) -> None:
        relative = Path(path.lstrip("/"))
        if ".." in relative.parts:
            self._send_error_json(HTTPStatus.FORBIDDEN, "invalid path")
            return
        target = WEB_ROOT / relative
        if not target.is_file():
            self._send_error_json(HTTPStatus.NOT_FOUND, f"no such file: {path}")
            return
        body = target.read_bytes()
        content_type = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
        if content_type.startswith("text/") or target.suffix in (".js", ".css", ".json"):
            content_type += "; charset=utf-8"
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _send_avatar(self, query: dict[str, list[str]]) -> None:
        """Proxy the avatar from the bancho.py avatar host, so the phone only
        ever talks to this machine."""
        player_id = (query.get("id") or [""])[0]
        base = self.config.avatar_url
        if not base or not player_id.isdigit():
            self._send_error_json(HTTPStatus.NOT_FOUND, "no avatar")
            return
        try:
            with urllib.request.urlopen(f"{base}/{player_id}", timeout=8) as response:
                body = response.read()
                content_type = response.headers.get("Content-Type", "image/png")
        except Exception:
            self._send_error_json(HTTPStatus.NOT_FOUND, "avatar unavailable")
            return
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "max-age=300")
        self.end_headers()
        self.wfile.write(body)

    def _send_background(self) -> None:
        """Serve the selected beatmap's background image from the Songs folder.

        The path comes from the game's own memory, but it is still resolved
        against the songs folder and rejected if it escapes, so a malformed
        map cannot turn this into an arbitrary file read.
        """
        state = self.manager.state()
        beatmap = state.get("beatmap") or {}
        folder = beatmap.get("folder") or ""
        background = beatmap.get("background") or ""
        songs = self.config.songs_folder
        if not (songs and folder and background):
            self._send_error_json(HTTPStatus.NOT_FOUND, "no background")
            return
        root = Path(songs).resolve()
        try:
            target = (root / folder / background).resolve()
        except OSError:
            self._send_error_json(HTTPStatus.NOT_FOUND, "bad background path")
            return
        if not target.is_relative_to(root) or not target.is_file():
            self._send_error_json(HTTPStatus.NOT_FOUND, "background not found")
            return
        body = target.read_bytes()
        content_type = mimetypes.guess_type(target.name)[0] or "image/jpeg"
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "max-age=3600")
        self.end_headers()
        self.wfile.write(body)

    def _stream(self, query: dict[str, list[str]]) -> None:
        """Push state as server-sent events until the client disconnects."""
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-cache, no-transform")
        self.send_header("Connection", "keep-alive")
        self.send_header("X-Accel-Buffering", "no")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()

        try:
            interval = float(query.get("interval", ["0"])[0])
        except ValueError:
            interval = 0.0
        min_interval = max(interval, self.config.poll_interval_ms / 1000.0)

        last_generation = -1
        last_write = _monotonic()
        while True:
            current = self.manager.generation
            now = _monotonic()
            if current != last_generation:
                payload = self.manager.state()
                try:
                    self.wfile.write(
                        b"data: "
                        + json.dumps(payload, ensure_ascii=False).encode()
                        + b"\n\n"
                    )
                    self.wfile.flush()
                except (BrokenPipeError, ConnectionAbortedError, ConnectionResetError, OSError):
                    return
                last_generation = current
                last_write = now
            elif now - last_write >= HEARTBEAT_SECONDS:
                try:
                    self.wfile.write(b": keep-alive\n\n")
                    self.wfile.flush()
                except (BrokenPipeError, ConnectionAbortedError, ConnectionResetError, OSError):
                    return
                last_write = now
            else:
                # Nothing new yet; idle until the reader publishes.
                self.manager.wait_for_update(current, timeout=min_interval)


def _monotonic() -> float:
    import time

    return time.monotonic()


class OverlayServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True
    verbose = False

    def handle_error(self, request: Any, client_address: Any) -> None:
        """Ignore clients that vanish.

        Browsers drop SSE connections routinely (tab closed, phone locked,
        EventSource reconnect), and socketserver would otherwise dump a
        traceback for each one.
        """
        import sys

        exc = sys.exc_info()[1]
        if isinstance(exc, (ConnectionError, BrokenPipeError, TimeoutError)):
            return
        super().handle_error(request, client_address)


def serve(config: Config, manager: StateManager) -> OverlayServer:
    handler = type(
        "BoundOverlayHandler",
        (OverlayHandler,),
        {"manager": manager, "config": config},
    )
    server = OverlayServer((config.host, config.port), handler)
    return server
