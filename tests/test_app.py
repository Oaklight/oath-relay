"""HTTP behaviour: auth, read-only GETs, and counter consumption.

The server runs in a child process, not a thread: the vendored ``httpserver``
calls ``loop.add_signal_handler`` during startup, which only works on the
main thread of the main interpreter.
"""

from __future__ import annotations

import base64
import contextlib
import json
import multiprocessing as mp
import socket
import time
import urllib.error
import urllib.parse
import urllib.request

import pytest

from oath_relay.app import create_app
from oath_relay.config import Config, hash_password, parse_config
from oath_relay.otp import hotp

SECRET = "GEZDGNBVGY3TQOJQGEZDGNBVGY3TQOJQ"
CODES = ["755224", "287082", "359152", "969429"]


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def _serve(config: Config, port: int) -> None:
    create_app(config).run(host="127.0.0.1", port=port)


def _request(url: str, method: str = "GET", headers=None, data=None):
    req = urllib.request.Request(url, method=method, headers=headers or {}, data=data)
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            return resp.status, resp.read().decode()
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode()


def _build_config(tmp_path, overrides: dict) -> Config:
    raw = {
        "server": {"host": "127.0.0.1", "allow_peek": False},
        "state": {"path": str(tmp_path / "state.json")},
        "auth": {
            "basic_user": "alice",
            "basic_password_hash": hash_password("s3cret"),
            "bearer_tokens": ["tok-1"],
        },
        "tokens": [
            {"name": "hotp1", "type": "hotp", "secret": SECRET, "counter_start": 0},
            {"name": "totp1", "type": "totp", "secret": SECRET, "period": 30},
        ],
    }
    for section, values in overrides.items():
        if isinstance(values, dict) and isinstance(raw.get(section), dict):
            raw[section].update(values)
        else:
            raw[section] = values
    return parse_config(raw)


@contextlib.contextmanager
def running(tmp_path, **overrides):
    config = _build_config(tmp_path, overrides)
    port = _free_port()
    ctx = mp.get_context("fork")
    proc = ctx.Process(target=_serve, args=(config, port), daemon=True)
    proc.start()

    base = f"http://127.0.0.1:{port}"
    deadline = time.time() + 10
    while time.time() < deadline:
        if proc.exitcode is not None:
            raise RuntimeError(f"server exited early with code {proc.exitcode}")
        try:
            if _request(base + "/healthz")[0] == 200:
                break
        except OSError:
            time.sleep(0.05)
    else:
        proc.terminate()
        proc.join(timeout=5)
        raise RuntimeError("server did not become ready within 10s")

    try:
        yield base, config
    finally:
        proc.terminate()
        proc.join(timeout=5)
        if proc.is_alive():  # pragma: no cover
            proc.kill()
            proc.join(timeout=5)


BEARER = {"Authorization": "Bearer tok-1"}


def _basic(user: str, password: str) -> dict[str, str]:
    raw = base64.b64encode(f"{user}:{password}".encode()).decode()
    return {"Authorization": f"Basic {raw}"}


def test_healthz_needs_no_auth(tmp_path) -> None:
    with running(tmp_path) as (base, _):
        status, body = _request(base + "/healthz")
    assert status == 200
    assert json.loads(body)["status"] == "ok"


@pytest.mark.parametrize("path", ["/", "/api/tokens", "/api/tokens/hotp1/status"])
def test_protected_paths_reject_anonymous(tmp_path, path: str) -> None:
    with running(tmp_path) as (base, _):
        status, _ = _request(base + path)
    assert status == 401


def test_bearer_and_basic_both_work(tmp_path) -> None:
    with running(tmp_path) as (base, _):
        assert _request(base + "/api/tokens", headers=BEARER)[0] == 200
        assert _request(base + "/api/tokens", headers=_basic("alice", "s3cret"))[0] == 200
        assert _request(base + "/api/tokens", headers=_basic("alice", "wrong"))[0] == 401
        assert _request(base + "/api/tokens", headers={"Authorization": "Bearer nope"})[0] == 401


def test_auth_can_be_disabled(tmp_path) -> None:
    overrides = {"auth": {"basic_user": "", "basic_password_hash": "", "bearer_tokens": []}}
    with running(tmp_path, **overrides) as (base, config):
        assert config.auth_enabled is False
        assert _request(base + "/api/tokens")[0] == 200


def test_get_status_never_mutates(tmp_path) -> None:
    with running(tmp_path) as (base, _):
        for _ in range(5):
            status, body = _request(base + "/api/tokens/hotp1/status", headers=BEARER)
            assert status == 200
            assert json.loads(body)["counter"] == 0


def test_get_index_never_mutates(tmp_path) -> None:
    with running(tmp_path) as (base, _):
        for _ in range(3):
            assert _request(base + "/", headers=BEARER)[0] == 200
        body = _request(base + "/api/tokens/hotp1/status", headers=BEARER)[1]
        assert json.loads(body)["counter"] == 0


def test_post_next_consumes_counters_in_order(tmp_path) -> None:
    with running(tmp_path) as (base, _):
        for expected_counter, expected_code in enumerate(CODES):
            status, body = _request(base + "/api/tokens/hotp1/next", method="POST", headers=BEARER)
            assert status == 200
            payload = json.loads(body)
            assert payload["code"] == expected_code
            assert payload["counter"] == expected_counter
            assert payload["next_counter"] == expected_counter + 1
        body = _request(base + "/api/tokens/hotp1/status", headers=BEARER)[1]
        assert json.loads(body)["counter"] == len(CODES)


def test_unknown_token_is_404(tmp_path) -> None:
    with running(tmp_path) as (base, _):
        assert _request(base + "/api/tokens/nope/status", headers=BEARER)[0] == 404
        assert _request(base + "/api/tokens/nope/next", method="POST", headers=BEARER)[0] == 404


def test_peek_is_disabled_by_default(tmp_path) -> None:
    with running(tmp_path) as (base, _):
        assert _request(base + "/api/tokens/hotp1/peek", headers=BEARER)[0] == 403


def test_peek_does_not_consume(tmp_path) -> None:
    with running(tmp_path, server={"allow_peek": True}) as (base, _):
        status, body = _request(base + "/api/tokens/hotp1/peek", headers=BEARER)
        assert status == 200
        assert json.loads(body)["code"] == CODES[0]
        body = _request(base + "/api/tokens/hotp1/status", headers=BEARER)[1]
        assert json.loads(body)["counter"] == 0


def test_resync_moves_the_counter(tmp_path) -> None:
    with running(tmp_path) as (base, _):
        body = json.dumps({"counter": 2}).encode()
        status, _ = _request(
            base + "/api/tokens/hotp1/resync",
            method="POST",
            headers={**BEARER, "Content-Type": "application/json"},
            data=body,
        )
        assert status == 200
        _, payload = _request(base + "/api/tokens/hotp1/next", method="POST", headers=BEARER)
        assert json.loads(payload)["code"] == CODES[2]


def test_resync_rejects_bad_body(tmp_path) -> None:
    with running(tmp_path) as (base, _):
        for payload in (b"not json", b'{"counter": -1}', b'{"nope": 1}'):
            status, _ = _request(
                base + "/api/tokens/hotp1/resync",
                method="POST",
                headers={**BEARER, "Content-Type": "application/json"},
                data=payload,
            )
            assert status == 400


def test_resync_rejects_totp(tmp_path) -> None:
    with running(tmp_path) as (base, _):
        status, _ = _request(
            base + "/api/tokens/totp1/resync",
            method="POST",
            headers={**BEARER, "Content-Type": "application/json"},
            data=b'{"counter": 1}',
        )
    assert status == 400


def test_totp_token_is_time_based_and_stateless(tmp_path) -> None:
    with running(tmp_path) as (base, _):
        status, body = _request(base + "/api/tokens/totp1/next", method="POST", headers=BEARER)
        assert status == 200
        payload = json.loads(body)
        assert payload["type"] == "totp"
        assert payload["code"] == hotp(SECRET, payload["counter"])
        assert 1 <= payload["remaining"] <= 30


def test_ui_form_issues_a_code(tmp_path) -> None:
    with running(tmp_path) as (base, _):
        data = urllib.parse.urlencode({"name": "hotp1"}).encode()
        status, body = _request(
            base + "/ui/next",
            method="POST",
            headers={**BEARER, "Content-Type": "application/x-www-form-urlencoded"},
            data=data,
        )
        assert status == 200
        assert CODES[0] in body
        assert "<!doctype html>" in body.lower()


def test_ui_requires_auth(tmp_path) -> None:
    with running(tmp_path) as (base, _):
        data = urllib.parse.urlencode({"name": "hotp1"}).encode()
        status, _ = _request(
            base + "/ui/next",
            method="POST",
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            data=data,
        )
    assert status == 401
