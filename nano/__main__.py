"""
nano/__main__.py
Entry point for NanoSideband.

Usage:
    python -m nano [OPTIONS]

Options:
    --config DIR        Base config directory (default: ~/.nanosideband)
    --name NAME         Set / update display name and save
    --log-level LEVEL   DEBUG | INFO | WARNING | ERROR (default: WARNING)

    Modes (mutually exclusive):
    --headless          Run as silent background daemon (Phase 1 default)
    --tui               Launch Textual terminal UI      (Phase 4)
    --web               Launch Flask web UI             (Phase 5)

    Utilities:
    --whoami            Print own identity hash and exit
    --send HASH MSG     Send a single text message and exit
    --listen            Print incoming messages to stdout until Ctrl-C

    --version           Print version and exit
"""

import argparse
import logging
import signal
import sys
import time

from nano import __version__
from nano.config import NanoConfig


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="nano",
        description="NanoSideband — lightweight LXMF messenger",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    p.add_argument("--version", action="version", version=f"NanoSideband {__version__}")
    p.add_argument("--config", metavar="DIR", default=None,
                   help="Config directory (default: ~/.nanosideband)")
    p.add_argument("--name", metavar="NAME", default=None,
                   help="Set display name")
    p.add_argument("--log-level", metavar="LEVEL", default=None,
                   choices=["DEBUG","INFO","WARNING","ERROR"],
                   help="Log verbosity")

    # ── modes ──
    mode = p.add_mutually_exclusive_group()
    mode.add_argument("--headless", action="store_true",
                      help="Run daemon — prints received messages to log")
    mode.add_argument("--tui",      action="store_true",
                      help="Launch Textual terminal UI (Phase 4)")
    mode.add_argument("--web",      action="store_true",
                      help="Launch Flask web UI (Phase 5)")
    mode.add_argument("--listen",   action="store_true",
                      help="Print received messages to stdout until Ctrl-C")

    # ── utilities ──
    p.add_argument("--whoami", action="store_true",
                   help="Print own identity hash and exit")
    p.add_argument("--send", nargs=2, metavar=("HASH", "MSG"),
                   help="Send one text message and exit")

    return p


def setup_logging(level_str: str) -> None:
    level = getattr(logging, level_str.upper(), logging.WARNING)
    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    # ── Config ────────────────────────────────────────────────────────────────
    config = NanoConfig(args.config)

    # Apply CLI overrides
    log_level = args.log_level or config.get("log_level", "WARNING")
    setup_logging(log_level)

    if args.name:
        config["display_name"] = args.name
        config.save()
        print(f"Display name set to: {args.name!r}")

    # ── Import core (deferred so --help works without rns installed) ──────────
    from nano.core import NanoCore

    core = NanoCore(config)

    # ── --whoami: just boot, print hash, exit ─────────────────────────────────
    if args.whoami:
        try:
            core.start()
            print(f"Identity hash : {core.identity_hash}")
            print(f"Display name  : {config['display_name']}")
            print(f"Config dir    : {config.base_dir}")
            core.stop()
        except RuntimeError as exc:
            print(f"Error: {exc}", file=sys.stderr)
            return 1
        return 0

    # ── --send: send a single message, wait briefly for delivery, exit ────────
    if args.send:
        dest_hash, message_text = args.send
        try:
            core.start()
            msg_id = core.send_text(dest_hash, message_text)
            if msg_id:
                print(f"Message queued: {msg_id}")
                # Give the router a moment to attempt delivery
                time.sleep(3)
            else:
                print("Failed to queue message.", file=sys.stderr)
                return 1
            core.stop()
        except RuntimeError as exc:
            print(f"Error: {exc}", file=sys.stderr)
            return 1
        return 0

    # ── --listen: receive messages, print to stdout ───────────────────────────
    if args.listen:
        def _print_message(source_hash, display_name, content,
                           fields, timestamp, msg_hash, has_image, **_):
            name = display_name or source_hash
            img_tag = " [+image]" if has_image else ""
            print(f"\n[{name}]{img_tag}\n  {content}\n")

        core.on_message(_print_message)
        try:
            core.start()
            print(f"Listening as {core.identity_hash}")
            print(f"Display name : {config['display_name']}")
            print("Waiting for messages… (Ctrl-C to quit)\n")
            _wait_forever()
        except RuntimeError as exc:
            print(f"Error: {exc}", file=sys.stderr)
            return 1
        except KeyboardInterrupt:
            print("\nExiting.")
        finally:
            core.stop()
        return 0

    # ── --tui ─────────────────────────────────────────────────────────────────
    if args.tui:
        try:
            import curses  # built-in on Linux/Mac
        except ImportError:
            print("curses not available. On Windows run: pip install windows-curses",
                  file=sys.stderr)
            return 1
        from nano.tui import run_tui
        try:
            core.start()
            run_tui(core)
        except RuntimeError as exc:
            print(f"Error: {exc}", file=sys.stderr)
            return 1
        finally:
            core.stop()
        return 0

    # ── --web ─────────────────────────────────────────────────────────────────
    if args.web:
        try:
            from nano.webui import run_web
        except ImportError:
            print("Flask not installed. Run: pip install flask", file=sys.stderr)
            return 1
        try:
            core.start()
            port = int(config.get("web_port", 5000))
            run_web(core, host="0.0.0.0", port=port)
        except RuntimeError as exc:
            print(f"Error: {exc}", file=sys.stderr)
            return 1
        finally:
            core.stop()
        return 0

    # ── default / --headless ─────────────────────────────────────────────────
    # Daemon mode: start, announce, log received messages, run forever.
    def _log_message(source_hash, display_name, content,
                     fields, timestamp, msg_hash, has_image, **_):
        name = display_name or source_hash
        img = " [+image]" if has_image else ""
        logging.getLogger("nano.recv").info(
            "From %s%s: %s", name, img, content[:120]
        )

    core.on_message(_log_message)

    try:
        core.start()
        print(f"NanoSideband {__version__} running.")
        print(f"Identity : {core.identity_hash}")
        print(f"Name     : {config['display_name']}")
        print(f"Config   : {config.base_dir}")
        print("Press Ctrl-C to quit.\n")
        _wait_forever()
    except RuntimeError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("\nShutting down…")
    finally:
        core.stop()

    return 0


def _wait_forever() -> None:
    """Block the main thread without busy-looping."""
    stop_event = __import__("threading").Event()
    signal.signal(signal.SIGINT,  lambda *_: stop_event.set())
    signal.signal(signal.SIGTERM, lambda *_: stop_event.set())
    stop_event.wait()


if __name__ == "__main__":
    sys.exit(main())
