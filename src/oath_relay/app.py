"""HTTP layer: JSON API plus a minimal browser page.

The one invariant worth protecting: **GET never mutates state**. Browser
prefetchers, link previews and crawlers all issue GETs, and a HOTP counter
that advances on a prefetch is a counter that silently drifts. Only
``POST /api/tokens/<name>/next`` and ``POST /api/tokens/<name>/resync``
consume or move a counter.
"""

from __future__ import annotations

import base64
import binascii
import hmac
import html
import time
from typing import Any

from ._vendor.httpserver import App, JSONResponse, Response, abort
from .config import Config, TokenConfig, verify_password
from .otp import OtpError, counter_for, hotp, totp
from .store import StateError, StateStore

__all__ = ["create_app"]

_HTML = "text/html; charset=utf-8"


def _remaining(period: int, now: float | None = None) -> int:
    moment = time.time() if now is None else now
    return period - int(moment) % period


def _bearer_ok(request: Any, tokens: tuple[str, ...]) -> bool:
    header = request.headers.get("authorization", "")
    scheme, _, value = header.partition(" ")
    if scheme.lower() != "bearer":
        return False
    candidate = value.strip()
    return any(hmac.compare_digest(candidate, token) for token in tokens)


def _basic_ok(request: Any, user: str, password_hash: str) -> bool:
    header = request.headers.get("authorization", "")
    scheme, _, value = header.partition(" ")
    if scheme.lower() != "basic":
        return False
    try:
        decoded = base64.b64decode(value.strip(), validate=True).decode("utf-8")
    except (binascii.Error, UnicodeDecodeError):
        return False
    supplied_user, sep, supplied_password = decoded.partition(":")
    if not sep:
        return False
    user_ok = hmac.compare_digest(supplied_user, user)
    password_ok = verify_password(supplied_password, password_hash)
    return user_ok and password_ok


def create_app(config: Config) -> App:
    """Build the :class:`App` for ``config``."""
    store = StateStore(config.state_path)
    app = App()

    def token_or_404(name: str) -> TokenConfig:
        tok = config.token(name)
        if tok is None:
            abort(404, f"unknown token {name!r}")
        return tok

    def issue(tok: TokenConfig) -> dict[str, Any]:
        if tok.type == "hotp":
            counter = store.take(tok.name, tok.counter_start)
            code = hotp(tok.secret, counter, tok.digits, tok.algorithm)
            return {
                "name": tok.name,
                "type": "hotp",
                "code": code,
                "counter": counter,
                "next_counter": counter + 1,
            }
        counter = counter_for(tok.period)
        code = totp(tok.secret, tok.period, tok.digits, tok.algorithm)
        return {
            "name": tok.name,
            "type": "totp",
            "code": code,
            "counter": counter,
            "remaining": _remaining(tok.period),
        }

    def peek(tok: TokenConfig) -> dict[str, Any]:
        if tok.type == "hotp":
            counter = store.counter(tok.name, tok.counter_start)
            return {
                "name": tok.name,
                "type": "hotp",
                "code": hotp(tok.secret, counter, tok.digits, tok.algorithm),
                "counter": counter,
            }
        counter = counter_for(tok.period)
        return {
            "name": tok.name,
            "type": "totp",
            "code": totp(tok.secret, tok.period, tok.digits, tok.algorithm),
            "counter": counter,
            "remaining": _remaining(tok.period),
        }

    def status(tok: TokenConfig) -> dict[str, Any]:
        if tok.type == "hotp":
            return {
                "name": tok.name,
                "type": "hotp",
                "counter": store.counter(tok.name, tok.counter_start),
            }
        return {
            "name": tok.name,
            "type": "totp",
            "counter": counter_for(tok.period),
            "remaining": _remaining(tok.period),
        }

    # -- auth ------------------------------------------------------------

    @app.before_request
    async def _require_auth(request):
        if request.path in {"/healthz", "/health"}:
            return None
        if not config.auth_enabled:
            return None
        if config.bearer_enabled and _bearer_ok(request, config.bearer_tokens):
            return None
        if config.basic_enabled and _basic_ok(
            request, config.basic_user, config.basic_password_hash
        ):
            return None
        return Response(
            "unauthorized\n",
            status_code=401,
            headers={"WWW-Authenticate": 'Basic realm="oath-relay", charset="UTF-8"'},
        )

    @app.errorhandler(404)
    async def _not_found(request, exc):  # noqa: ARG001
        return JSONResponse({"error": "not found"}, status_code=404)

    @app.errorhandler(OtpError)
    @app.errorhandler(StateError)
    async def _bad_state(request, exc):  # noqa: ARG001
        return JSONResponse({"error": str(exc)}, status_code=500)

    # -- JSON API --------------------------------------------------------

    @app.get("/healthz")
    async def _healthz(request):  # noqa: ARG001
        return {"status": "ok"}

    @app.get("/api/tokens")
    async def _list_tokens(request):  # noqa: ARG001
        return {"tokens": [status(tok) for tok in config.tokens]}

    @app.get("/api/tokens/<name>/status")
    async def _token_status(request, name: str):  # noqa: ARG001
        return status(token_or_404(name))

    @app.post("/api/tokens/<name>/next")
    async def _token_next(request, name: str):  # noqa: ARG001
        return issue(token_or_404(name))

    @app.post("/api/tokens/<name>/resync")
    async def _token_resync(request, name: str):
        tok = token_or_404(name)
        if tok.type != "hotp":
            abort(400, "resync only applies to hotp tokens")
        try:
            payload = request.json() or {}
        except ValueError:
            abort(400, "body must be JSON")
        if not isinstance(payload, dict) or "counter" not in payload:
            abort(400, 'body must be {"counter": <int>}')
        value = payload["counter"]
        if not isinstance(value, int) or isinstance(value, bool) or value < 0:
            abort(400, "counter must be a non-negative integer")
        store.set_counter(tok.name, value)
        return {"name": tok.name, "counter": value}

    @app.get("/api/tokens/<name>/peek")
    async def _token_peek(request, name: str):  # noqa: ARG001
        if not config.allow_peek:
            abort(403, "peek is disabled; set server.allow_peek = true to enable")
        return peek(token_or_404(name))

    # -- browser UI ------------------------------------------------------

    def render_index(codes: dict[str, dict[str, Any]] | None = None) -> str:
        codes = codes or {}
        rows = []
        for tok in config.tokens:
            current = status(tok)
            detail = (
                f"counter {current['counter']}"
                if tok.type == "hotp"
                else f"rotates in {current['remaining']}s"
            )
            result = codes.get(tok.name)
            result_html = (
                f'<div class="code">{html.escape(result["code"])}</div>'
                f'<div class="meta">counter {result["counter"]}'
                + (f" · rotates in {result['remaining']}s" if "remaining" in result else "")
                + "</div>"
                if result
                else ""
            )
            rows.append(
                f"""
        <section class="token">
          <h2>{html.escape(tok.display)}</h2>
          <p class="meta">{html.escape(tok.type.upper())} · {html.escape(detail)}</p>
          {result_html}
          <form method="post" action="/ui/next">
            <input type="hidden" name="name" value="{html.escape(tok.name)}">
            <button type="submit">Get code</button>
          </form>
        </section>"""
            )
        body = "".join(rows) or "<p>No tokens configured.</p>"
        return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>oath-relay</title>
<style>
  :root {{ color-scheme: light dark; }}
  body {{ font: 16px/1.5 system-ui, sans-serif; margin: 0 auto; max-width: 40rem; padding: 1.5rem; }}
  h1 {{ font-size: 1.25rem; }}
  .token {{ border: 1px solid color-mix(in srgb, currentColor 20%, transparent);
            border-radius: .5rem; padding: 1rem; margin-bottom: 1rem; }}
  .token h2 {{ font-size: 1rem; margin: 0 0 .25rem; }}
  .meta {{ opacity: .7; font-size: .875rem; margin: 0; }}
  .code {{ font: 700 2.25rem/1.2 ui-monospace, monospace; letter-spacing: .15em; margin: .75rem 0 .25rem; }}
  button {{ font: inherit; padding: .5rem 1rem; border-radius: .375rem; cursor: pointer; }}
</style>
</head>
<body>
<h1>oath-relay</h1>
{body}
</body>
</html>
"""

    @app.get("/")
    async def _index(request):  # noqa: ARG001
        return Response(render_index(), content_type=_HTML)

    @app.post("/ui/next")
    async def _ui_next(request):
        form = request.form()
        names = form.get("name") or []
        if not names:
            abort(400, "missing name")
        tok = token_or_404(names[0])
        result = issue(tok)
        return Response(render_index({tok.name: result}), content_type=_HTML)

    return app
