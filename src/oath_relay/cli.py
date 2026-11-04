"""Command line interface."""

from __future__ import annotations

import argparse
import getpass
import sys
import time
from pathlib import Path

from . import __version__
from .app import create_app
from .config import (
    Config,
    ConfigError,
    hash_password,
    load_config,
)
from .otp import OtpError, counter_for, hotp, totp
from .store import StateError, StateStore

__all__ = ["main"]

_TEMPLATE = """\
# oath-relay configuration.
# Secrets live here; counters live in the state file. Never commit this file.

[server]
host = "127.0.0.1"
port = 8790
allow_peek = false

[state]
path = "./state/state.json"

[auth]
# Leave a section empty to disable that scheme. With both empty the service
# is unauthenticated and only safe on a private network.
basic_user = ""
basic_password_hash = ""
bearer_tokens = []

[[tokens]]
name = "example-hotp"
type = "hotp"
secret = "JBSWY3DPEHPK3PXP"
digits = 6
algorithm = "sha1"
counter_start = 0

# [[tokens]]
# name = "example-totp"
# type = "totp"
# secret = "JBSWY3DPEHPK3PXP"
# digits = 6
# period = 30
"""


def _load(args: argparse.Namespace) -> Config:
    return load_config(args.config)


def _token(config: Config, name: str):
    tok = config.token(name)
    if tok is None:
        known = ", ".join(t.name for t in config.tokens) or "(none)"
        raise SystemExit(f"error: unknown token {name!r}; configured: {known}")
    return tok


def _issue(config: Config, name: str, *, peek: bool) -> tuple[str, int, int | None]:
    tok = _token(config, name)
    if tok.type == "totp":
        return (
            totp(tok.secret, tok.period, tok.digits, tok.algorithm),
            counter_for(tok.period),
            (tok.period - int(time.time()) % tok.period),
        )
    store = StateStore(config.state_path)
    counter = (
        store.counter(tok.name, tok.counter_start)
        if peek
        else store.take(tok.name, tok.counter_start)
    )
    return hotp(tok.secret, counter, tok.digits, tok.algorithm), counter, None


def _cmd_serve(args: argparse.Namespace) -> int:
    config = _load(args)
    host = args.host or config.host
    port = args.port or config.port

    if not config.tokens:
        print("warning: no [[tokens]] configured", file=sys.stderr)
    if not config.auth_enabled:
        print(
            "warning: authentication is disabled; anyone who can reach "
            f"{host}:{port} can consume your counters",
            file=sys.stderr,
        )

    app = create_app(config)
    print(f"oath-relay {__version__} listening on http://{host}:{port}", file=sys.stderr)
    app.run(host=host, port=port)
    return 0


def _cmd_code(args: argparse.Namespace) -> int:
    config = _load(args)
    code, counter, remaining = _issue(config, args.name, peek=False)
    if args.quiet:
        print(code)
        return 0
    suffix = f"  (rotates in {remaining}s)" if remaining is not None else f"  (counter {counter})"
    print(f"{args.name}: {code}{suffix}")
    return 0


def _cmd_peek(args: argparse.Namespace) -> int:
    config = _load(args)
    code, counter, remaining = _issue(config, args.name, peek=True)
    if args.quiet:
        print(code)
        return 0
    suffix = (
        f"  (rotates in {remaining}s)"
        if remaining is not None
        else f"  (counter {counter}, not consumed)"
    )
    print(f"{args.name}: {code}{suffix}")
    return 0


def _cmd_list(args: argparse.Namespace) -> int:
    config = _load(args)
    store = StateStore(config.state_path)
    if not config.tokens:
        print("no tokens configured")
        return 0
    width = max(len(tok.name) for tok in config.tokens)
    for tok in config.tokens:
        if tok.type == "hotp":
            state = f"counter {store.counter(tok.name, tok.counter_start)}"
        else:
            state = f"rotates in {tok.period - int(time.time()) % tok.period}s"
        print(f"{tok.name:<{width}}  {tok.type:<4}  {state}")
    return 0


def _cmd_status(args: argparse.Namespace) -> int:
    config = _load(args)
    tok = _token(config, args.name)
    if tok.type == "hotp":
        print(StateStore(config.state_path).counter(tok.name, tok.counter_start))
    else:
        print(tok.period - int(time.time()) % tok.period)
    return 0


def _cmd_resync(args: argparse.Namespace) -> int:
    config = _load(args)
    tok = _token(config, args.name)
    if tok.type != "hotp":
        raise SystemExit("error: resync only applies to hotp tokens")
    StateStore(config.state_path).set_counter(tok.name, args.counter)
    print(f"{tok.name}: counter set to {args.counter}")
    return 0


def _cmd_init(args: argparse.Namespace) -> int:
    target = Path(args.path)
    if target.exists() and not args.force:
        raise SystemExit(f"error: {target} already exists (use --force to overwrite)")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(_TEMPLATE, encoding="utf-8")
    target.chmod(0o600)
    print(f"wrote {target}")
    return 0


def _cmd_hash(args: argparse.Namespace) -> int:
    password = args.password or getpass.getpass("password: ")
    if not password:
        raise SystemExit("error: empty password")
    if not args.password:
        again = getpass.getpass("repeat: ")
        if again != password:
            raise SystemExit("error: passwords do not match")
    print(hash_password(password))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="oath-relay",
        description="Share a stateful HOTP token across devices from one counter.",
    )
    parser.add_argument("--version", action="version", version=f"oath-relay {__version__}")
    parser.add_argument(
        "-c",
        "--config",
        help="path to config.toml (default: ./oath-relay.toml, /config/config.toml, "
        "/etc/oath-relay/config.toml)",
    )
    # Allow --config both before and after the subcommand. The subparser copy
    # uses SUPPRESS so it does not overwrite a value given before the command.
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("-c", "--config", default=argparse.SUPPRESS)
    sub = parser.add_subparsers(dest="command", required=True)

    serve = sub.add_parser("serve", parents=[common], help="run the HTTP server")
    serve.add_argument("--host", help="override server.host")
    serve.add_argument("--port", type=int, help="override server.port")
    serve.set_defaults(func=_cmd_serve)

    code = sub.add_parser(
        "code", parents=[common], help="print the next code and consume the counter"
    )
    code.add_argument("name")
    code.add_argument("-q", "--quiet", action="store_true", help="print the code only")
    code.set_defaults(func=_cmd_code)

    peek = sub.add_parser(
        "peek", parents=[common], help="print the current code without consuming it"
    )
    peek.add_argument("name")
    peek.add_argument("-q", "--quiet", action="store_true", help="print the code only")
    peek.set_defaults(func=_cmd_peek)

    listing = sub.add_parser("list", parents=[common], help="list configured tokens")
    listing.set_defaults(func=_cmd_list)

    status = sub.add_parser("status", parents=[common], help="show a token's counter")
    status.add_argument("name")
    status.set_defaults(func=_cmd_status)

    resync = sub.add_parser("resync", parents=[common], help="force a HOTP counter (recovery)")
    resync.add_argument("name")
    resync.add_argument("counter", type=int)
    resync.set_defaults(func=_cmd_resync)

    init = sub.add_parser("init", parents=[common], help="write a template configuration file")
    init.add_argument("path", nargs="?", default="oath-relay.toml")
    init.add_argument("-f", "--force", action="store_true")
    init.set_defaults(func=_cmd_init)

    hashing = sub.add_parser(
        "hash", parents=[common], help="hash a password for auth.basic_password_hash"
    )
    hashing.add_argument("-p", "--password", help="password (omit to prompt)")
    hashing.set_defaults(func=_cmd_hash)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except (ConfigError, OtpError, StateError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
