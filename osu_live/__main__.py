"""osu! live: stream a local osu! stable client to any phone on your network.

Reads the game's state straight out of its process memory, calculates pp with the
same library bancho.py uses, optionally cross-references a bancho.py server, and
serves the lot over HTTP with server-sent events.
"""

from __future__ import annotations

import argparse
import signal
import sys
import threading

from .config import Config
from .paths import DATA_ROOT, WEB_ROOT
from .server import local_addresses, serve
from .state import StateManager


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="osu-live", description=__doc__)
    parser.add_argument("--host", help="interface to bind (default 0.0.0.0)")
    parser.add_argument("--port", type=int, help="port to listen on (default 24051)")
    parser.add_argument("--songs", help="path to the osu! Songs folder, for pp")
    parser.add_argument("--bancho", help="bancho.py API base, e.g. https://api.example.com")
    parser.add_argument("--player", help="player id or name on that server")
    parser.add_argument("--token", help="require this access token from clients")
    parser.add_argument("--verbose", action="store_true", help="log every HTTP request")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    config = Config.from_env()

    # The banner below carries the address the phone has to be pointed at, so
    # it must not sit in a buffer. Python line-buffers a terminal but blocks
    # anything redirected to a file or a service log, where the address would
    # then not appear until the program exits -- which, for this program, is
    # when it is no longer useful.
    try:
        sys.stdout.reconfigure(line_buffering=True)
    except (AttributeError, OSError):
        pass

    if args.host:
        config.host = args.host
    if args.port:
        config.port = args.port
    if args.songs:
        config.songs_folder = args.songs
    if args.bancho:
        config.bancho_api_url = args.bancho.rstrip("/")
    if args.player:
        config.bancho_player = args.player
    if args.token:
        config.access_token = args.token

    # A build that lost its web assets would otherwise serve 404s to the phone
    # and look like a networking problem. Checked before the port is bound, so
    # bailing out here does not leave a socket behind.
    if not WEB_ROOT.is_dir():
        print(f"the web assets are missing from {WEB_ROOT}", file=sys.stderr)
        print("rebuild with `pyinstaller osu-live.spec`", file=sys.stderr)
        return 1

    manager = StateManager(config)
    server = serve(config, manager)
    server.verbose = args.verbose  # type: ignore[attr-defined]

    manager.start()
    addresses = local_addresses(config.port)
    print("osu! live is running")
    print(f"  local   http://127.0.0.1:{config.port}")
    for address in addresses:
        print(f"  phone   {address}")
    if config.access_token:
        print("  token   required (?token=… or Authorization: Bearer …)")
    if config.songs_folder:
        print(f"  songs   {config.songs_folder}")
    else:
        # Without this the overlay just shows no pp and no artwork, which
        # looks like a broken program rather than a missing setting.
        print("  songs   NOT FOUND -- pp and the map artwork need it")
        print("          set SONGS_FOLDER in .env, or start osu! first")
    if config.bancho_api_url:
        print(f"  bancho  {config.bancho_api_url} ({config.bancho_player or 'no player set'})")
        if not config.avatar_url:
            print("  avatar  not set -- the overlay has no avatar (AVATAR_URL)")
    print(f"  data    {DATA_ROOT}")
    print("\nPress Ctrl+C to stop.")

    stop = threading.Event()

    def shutdown(*_: object) -> None:
        stop.set()

    signal.signal(signal.SIGINT, shutdown)
    try:
        signal.signal(signal.SIGTERM, shutdown)
    except (AttributeError, ValueError):
        pass

    thread = threading.Thread(target=server.serve_forever, name="http", daemon=True)
    thread.start()
    try:
        while not stop.is_set():
            stop.wait(0.5)
    finally:
        print("\nstopping…")
        server.shutdown()
        server.server_close()
        manager.stop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
