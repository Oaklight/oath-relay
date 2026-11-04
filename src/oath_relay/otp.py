"""OATH one-time password generation (RFC 4226 HOTP, RFC 6238 TOTP).

Stdlib only. The HOTP construction is the whole point of this package: a
code is ``HMAC(secret, counter)``, so producing the *next* code requires
shared mutable state. ``totp`` is provided for completeness.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import time

__all__ = [
    "ALGORITHMS",
    "OtpError",
    "counter_for",
    "hotp",
    "normalize_secret",
    "totp",
]

ALGORITHMS = {
    "sha1": hashlib.sha1,
    "sha256": hashlib.sha256,
    "sha512": hashlib.sha512,
}

MIN_DIGITS = 1
MAX_DIGITS = 10


class OtpError(ValueError):
    """Raised for an unusable secret, counter, or parameter."""


def normalize_secret(secret: str | bytes) -> bytes:
    """Decode a base32 secret, tolerating whitespace, case and padding noise.

    Duo hands out secrets whose base32 encoding ends in ``====``; users paste
    them with or without padding, in either case, sometimes with spaces from
    an OCR'd QR code. All of that is accepted here.
    """
    if isinstance(secret, bytes):
        try:
            secret = secret.decode("ascii")
        except UnicodeDecodeError as exc:
            raise OtpError("secret must be ASCII base32") from exc
    if not isinstance(secret, str):
        raise OtpError("secret must be a base32 string")

    cleaned = "".join(secret.split()).upper().rstrip("=")
    if not cleaned:
        raise OtpError("secret is empty")
    pad = (-len(cleaned)) % 8
    try:
        return base64.b32decode(cleaned + "=" * pad, casefold=True)
    except (binascii.Error, ValueError) as exc:
        raise OtpError(f"invalid base32 secret: {exc}") from exc


def _truncate(digest: bytes) -> int:
    """RFC 4226 section 5.3 dynamic truncation."""
    offset = digest[-1] & 0x0F
    return (
        ((digest[offset] & 0x7F) << 24)
        | ((digest[offset + 1] & 0xFF) << 16)
        | ((digest[offset + 2] & 0xFF) << 8)
        | (digest[offset + 3] & 0xFF)
    )


def _validate(digits: int, algorithm: str) -> None:
    if not isinstance(digits, int) or not MIN_DIGITS <= digits <= MAX_DIGITS:
        raise OtpError(f"digits must be an integer in {MIN_DIGITS}..{MAX_DIGITS}")
    if algorithm.lower() not in ALGORITHMS:
        raise OtpError(f"unsupported algorithm {algorithm!r}")


def hotp(
    secret: str | bytes,
    counter: int,
    digits: int = 6,
    algorithm: str = "sha1",
) -> str:
    """Return the HOTP code for ``counter``.

    ``secret`` may be a base32 string (any padding/case) or already-decoded
    key bytes.
    """
    _validate(digits, algorithm)
    if not isinstance(counter, int) or isinstance(counter, bool):
        raise OtpError("counter must be an integer")
    if counter < 0 or counter >= 1 << 64:
        raise OtpError("counter must be in 0..2**64-1")

    key = secret if isinstance(secret, bytes) else normalize_secret(secret)
    message = counter.to_bytes(8, "big")
    digest = hmac.new(key, message, ALGORITHMS[algorithm.lower()]).digest()
    return str(_truncate(digest) % (10**digits)).zfill(digits)


def counter_for(period: int = 30, at: float | None = None) -> int:
    """Return the TOTP time-step for ``at`` (defaults to now)."""
    if not isinstance(period, int) or period <= 0:
        raise OtpError("period must be a positive integer")
    now = time.time() if at is None else at
    return int(now) // period


def totp(
    secret: str | bytes,
    period: int = 30,
    digits: int = 6,
    algorithm: str = "sha1",
    at: float | None = None,
) -> str:
    """Return the TOTP code for ``at`` (defaults to now)."""
    return hotp(secret, counter_for(period, at), digits, algorithm)
