"""Configuration loading: TOML on disk, environment variables on top.

Secrets live here; counters live in the state file (see :mod:`oath_relay.store`).
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import os
import secrets
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .otp import ALGORITHMS

__all__ = [
    "ConfigError",
    "Config",
    "TokenConfig",
    "default_config_paths",
    "hash_password",
    "load_config",
    "parse_config",
    "verify_password",
]

DEFAULT_CONFIG_PATHS = (
    "oath-relay.toml",
    "/config/config.toml",
    "/etc/oath-relay/config.toml",
)

_TOKEN_NAME_ALLOWED = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-.")

_SCRYPT_N = 2**14
_SCRYPT_R = 8
_SCRYPT_P = 1
_SCRYPT_DKLEN = 32


class ConfigError(ValueError):
    """Raised for a malformed or contradictory configuration."""


# -- password hashing ------------------------------------------------------


def hash_password(password: str) -> str:
    """Return a self-describing scrypt hash string."""
    if not password:
        raise ConfigError("password must not be empty")
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(
        password.encode("utf-8"),
        salt=salt,
        n=_SCRYPT_N,
        r=_SCRYPT_R,
        p=_SCRYPT_P,
        dklen=_SCRYPT_DKLEN,
    )
    return "$".join(
        [
            "scrypt",
            str(_SCRYPT_N),
            str(_SCRYPT_R),
            str(_SCRYPT_P),
            base64.b64encode(salt).decode("ascii"),
            base64.b64encode(digest).decode("ascii"),
        ]
    )


def verify_password(password: str, encoded: str) -> bool:
    """Constant-time check of a password against :func:`hash_password` output."""
    if not password or not encoded:
        return False
    try:
        scheme, n, r, p, salt_b64, hash_b64 = encoded.split("$")
    except ValueError:
        return False
    if scheme != "scrypt":
        return False
    try:
        salt = base64.b64decode(salt_b64)
        expected = base64.b64decode(hash_b64)
        digest = hashlib.scrypt(
            password.encode("utf-8"),
            salt=salt,
            n=int(n),
            r=int(r),
            p=int(p),
            dklen=len(expected),
        )
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(digest, expected)


# -- data model ------------------------------------------------------------


@dataclass(frozen=True)
class TokenConfig:
    name: str
    type: str = "hotp"
    secret: str = ""
    digits: int = 6
    algorithm: str = "sha1"
    period: int = 30
    counter_start: int = 0
    label: str = ""

    @property
    def display(self) -> str:
        return self.label or self.name


@dataclass(frozen=True)
class Config:
    host: str = "127.0.0.1"
    port: int = 8790
    allow_peek: bool = False
    state_path: str = "./state/state.json"
    basic_user: str = ""
    basic_password_hash: str = ""
    bearer_tokens: tuple[str, ...] = ()
    tokens: tuple[TokenConfig, ...] = ()
    source: str = ""

    @property
    def basic_enabled(self) -> bool:
        return bool(self.basic_user and self.basic_password_hash)

    @property
    def bearer_enabled(self) -> bool:
        return bool(self.bearer_tokens)

    @property
    def auth_enabled(self) -> bool:
        return self.basic_enabled or self.bearer_enabled

    def token(self, name: str) -> TokenConfig | None:
        for tok in self.tokens:
            if tok.name == name:
                return tok
        return None


# -- parsing ---------------------------------------------------------------


def _as_bool(value: Any, where: str) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in {"1", "true", "yes", "on"}:
            return True
        if lowered in {"0", "false", "no", "off"}:
            return False
    raise ConfigError(f"{where} must be a boolean")


def _as_int(value: Any, where: str, *, minimum: int | None = None) -> int:
    if isinstance(value, bool):
        raise ConfigError(f"{where} must be an integer")
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise ConfigError(f"{where} must be an integer") from exc
    if minimum is not None and parsed < minimum:
        raise ConfigError(f"{where} must be >= {minimum}")
    return parsed


def _parse_token(raw: Any, index: int) -> TokenConfig:
    where = f"tokens[{index}]"
    if not isinstance(raw, dict):
        raise ConfigError(f"{where} must be a table")

    name = str(raw.get("name", "")).strip()
    if not name:
        raise ConfigError(f"{where}.name is required")
    if any(ch not in _TOKEN_NAME_ALLOWED for ch in name):
        raise ConfigError(f"{where}.name may only contain letters, digits, '_', '-', '.'")

    kind = str(raw.get("type", "hotp")).strip().lower()
    if kind not in {"hotp", "totp"}:
        raise ConfigError(f"{where}.type must be 'hotp' or 'totp'")

    secret = str(raw.get("secret", "")).strip()
    if not secret:
        raise ConfigError(f"{where}.secret is required")

    algorithm = str(raw.get("algorithm", "sha1")).strip().lower()
    if algorithm not in ALGORITHMS:
        raise ConfigError(f"{where}.algorithm must be one of {', '.join(sorted(ALGORITHMS))}")

    digits = _as_int(raw.get("digits", 6), f"{where}.digits", minimum=1)
    if digits > 10:
        raise ConfigError(f"{where}.digits must be <= 10")

    period = _as_int(raw.get("period", 30), f"{where}.period", minimum=1)
    counter_start = _as_int(raw.get("counter_start", 0), f"{where}.counter_start", minimum=0)

    return TokenConfig(
        name=name,
        type=kind,
        secret=secret,
        digits=digits,
        algorithm=algorithm,
        period=period,
        counter_start=counter_start,
        label=str(raw.get("label", "")).strip(),
    )


def _apply_env(raw: dict[str, Any], environ: dict[str, str] | None = None) -> None:
    """Overlay ``OATH_RELAY_<SECTION>__<KEY>`` environment variables."""
    env = os.environ if environ is None else environ
    prefix = "OATH_RELAY_"
    for key, value in env.items():
        if not key.startswith(prefix):
            continue
        path = key[len(prefix) :].lower().split("__")
        if len(path) != 2:
            continue
        section, field_name = path
        if section not in {"server", "state", "auth"}:
            continue
        raw.setdefault(section, {})
        if not isinstance(raw[section], dict):
            raise ConfigError(f"[{section}] must be a table")
        if section == "auth" and field_name == "bearer_tokens":
            raw[section][field_name] = [item.strip() for item in value.split(",") if item.strip()]
        else:
            raw[section][field_name] = value


def parse_config(raw: dict[str, Any], *, source: str = "") -> Config:
    """Build a :class:`Config` from an already-parsed mapping."""
    if not isinstance(raw, dict):
        raise ConfigError("configuration must be a table")

    server = raw.get("server") or {}
    state = raw.get("state") or {}
    auth = raw.get("auth") or {}
    for section, value in (("server", server), ("state", state), ("auth", auth)):
        if not isinstance(value, dict):
            raise ConfigError(f"[{section}] must be a table")

    tokens_raw = raw.get("tokens") or []
    if not isinstance(tokens_raw, list):
        raise ConfigError("[[tokens]] must be an array of tables")

    tokens = tuple(_parse_token(item, index) for index, item in enumerate(tokens_raw))
    seen: set[str] = set()
    for tok in tokens:
        if tok.name in seen:
            raise ConfigError(f"duplicate token name {tok.name!r}")
        seen.add(tok.name)

    bearer = auth.get("bearer_tokens") or []
    if isinstance(bearer, str):
        bearer = [item.strip() for item in bearer.split(",") if item.strip()]
    if not isinstance(bearer, (list, tuple)):
        raise ConfigError("auth.bearer_tokens must be an array of strings")
    bearer_tokens = tuple(str(item).strip() for item in bearer if str(item).strip())

    return Config(
        host=str(server.get("host", "127.0.0.1")),
        port=_as_int(server.get("port", 8790), "server.port", minimum=1),
        allow_peek=_as_bool(server.get("allow_peek", False), "server.allow_peek"),
        state_path=str(state.get("path", "./state/state.json")),
        basic_user=str(auth.get("basic_user", "")).strip(),
        basic_password_hash=str(auth.get("basic_password_hash", "")).strip(),
        bearer_tokens=bearer_tokens,
        tokens=tokens,
        source=source,
    )


def default_config_paths() -> tuple[str, ...]:
    return DEFAULT_CONFIG_PATHS


def load_config(
    path: str | os.PathLike[str] | None = None,
    *,
    environ: dict[str, str] | None = None,
) -> Config:
    """Load configuration from ``path`` (or the default search path) plus env."""
    if path is None:
        for candidate in DEFAULT_CONFIG_PATHS:
            if Path(candidate).is_file():
                path = candidate
                break
        else:
            raise ConfigError(
                "no configuration file found; looked in: " + ", ".join(DEFAULT_CONFIG_PATHS)
            )

    config_path = Path(path)
    try:
        with config_path.open("rb") as fh:
            raw = tomllib.load(fh)
    except FileNotFoundError as exc:
        raise ConfigError(f"configuration file not found: {config_path}") from exc
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(f"{config_path} is not valid TOML: {exc}") from exc

    _apply_env(raw, environ)
    return parse_config(raw, source=str(config_path))
