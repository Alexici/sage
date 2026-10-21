"""Command line for the server.

  python -m sage.server devices add laptop      pair a device; prints its token ONCE
  python -m sage.server devices list
  python -m sage.server devices remove laptop
  python -m sage.server serve [--lan] [--port 8765] [--allow 192.168.1.0/24] [any chat option: --backend, --ref ...]
  python -m sage.server serve --demo            scripted Sage with no model, GPU or audio, for building a client
"""
import argparse
import logging
import socket
import sys
from pathlib import Path

from sage.paths import ROOT
from sage.server.auth import Allowlist, DeviceStore

DEVICES_FILE = ROOT / "data" / "devices.json"
DEFAULT_PORT = 8765


def lan_address() -> str:
    """Best guess of this machine's address on the home network (no traffic is sent)."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("10.255.255.255", 1))
            return s.getsockname()[0]
    except OSError:
        return "<this machine's address>"


def devices_main(argv: list) -> None:
    ap = argparse.ArgumentParser(prog="sage.server devices")
    ap.add_argument("action", choices=["add", "list", "remove"])
    ap.add_argument("name", nargs="?")
    ap.add_argument("--file", type=Path, default=DEVICES_FILE)
    args = ap.parse_args(argv)
    store = DeviceStore(args.file)
    if args.action == "list":
        names = store.names()
        print("\n".join(names) if names else "No devices are paired yet.")
        return
    if not args.name:
        sys.exit(f"devices {args.action} needs a name")
    if args.action == "add":
        try:
            token = store.add(args.name)
        except ValueError as exc:
            sys.exit(str(exc))
        print(f"Paired {args.name!r}. Put this token in the app's Settings; it is shown only now:\n\n  {token}\n")
    elif store.remove(args.name):
        print(f"Removed {args.name!r}; it is disconnected within a few seconds.")
    else:
        sys.exit(f"No device called {args.name!r}.")


def serve_main(argv: list) -> None:
    import uvicorn

    from sage.cli import build_engine, build_parser, make_listener
    from sage.server.app import create_app
    from sage.server.demo import make_demo_session
    from sage.server.protocol import MAX_FRAME

    ap = build_parser()                         # every chat option works here too
    ap.prog = "sage.server serve"
    ap.add_argument("--demo", action="store_true", help="scripted Sage: no model, GPU or audio (for building a client)")
    ap.add_argument("--host", default=None, help="address to listen on (default 127.0.0.1)")
    ap.add_argument("--lan", action="store_true", help="listen on the home network (0.0.0.0) instead of this machine only")
    ap.add_argument("--port", type=int, default=DEFAULT_PORT)
    ap.add_argument("--allow", action="append", default=[], metavar="CIDR",
                    help="only accept these networks (and this machine), e.g. 192.168.1.0/24; repeatable")
    ap.add_argument("--devices-file", type=Path, default=DEVICES_FILE)
    ap.add_argument("--no-stt", action="store_true",
                    help="do not load speech recognition: the app's talk button is then unavailable")
    args = ap.parse_args(argv)

    try:
        allow = Allowlist(args.allow)
    except ValueError as exc:
        sys.exit(f"--allow: {exc}")
    host = args.host or ("0.0.0.0" if args.lan else "127.0.0.1")
    if args.host and args.lan:
        sys.exit("use either --host or --lan, not both")

    if args.demo:
        devices = DeviceStore(None)
        token = devices.add("demo")
        session = make_demo_session()
        print(f"DEMO MODE: no model, no audio. Device token (valid until this server stops):\n\n  {token}\n")
    else:
        devices = DeviceStore(args.devices_file)
        if not devices.names():
            sys.exit("No device is paired yet. Run:  python -m sage.server devices add <name>   "
                     "(for example 'pc'), then put the token in the app.")
        from sage.session import Session
        voice, toolbox, system = build_engine(args)
        listener = None
        if not args.no_stt:
            try:
                listener = make_listener(args, hold=True)
            except Exception as exc:   # noqa: BLE001 - no faster-whisper or no model: text chat still works
                print(f"Speech recognition is off ({exc}). Install it with: uv pip install faster-whisper "
                      "(or start with --no-stt to silence this).", file=sys.stderr)
        session = Session(args, voice, toolbox, system, listener=listener)
        print(f"Devices: {', '.join(devices.names())}")

    app = create_app(session, devices, allow)

    class _HideDenials(logging.Filter):
        """A refused connection (no token, a browser, a wrong address) is answered with 401/403 and already logged
        by us without any token; uvicorn would add an alarming ERROR line for the same thing."""
        def filter(self, record):
            return "without completing handshake" not in record.getMessage()
    logging.getLogger("uvicorn.error").addFilter(_HideDenials())
    where = lan_address() if host == "0.0.0.0" else host
    print(f"Sage server on http://{host}:{args.port}  (clients connect to {where}:{args.port})"
          + ("  [home network]" if host == "0.0.0.0" else "  [this machine only]"))
    uvicorn.run(app, host=host, port=args.port, log_level="warning", ws_max_size=MAX_FRAME,
                ws_ping_interval=20, ws_ping_timeout=20)


def main(argv=None) -> None:
    argv = list(sys.argv[1:] if argv is None else argv)
    cmd = argv.pop(0) if argv else ""
    if cmd == "devices":
        devices_main(argv)
    elif cmd == "serve":
        serve_main(argv)
    else:
        print(__doc__)
        sys.exit(0 if cmd in ("-h", "--help") else 2)


if __name__ == "__main__":
    main()
