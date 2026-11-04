"""Known-answer tests for HOTP/TOTP.

The RFC vectors prove the implementation is correct. The Duo vector test is
gated behind an environment variable so a real enrolment secret never lands
in the repository: set ``OATH_RELAY_DUO_SECRET`` to a base32 secret whose
codes at counters 0..3 are known.
"""

from __future__ import annotations

import os

import pytest

from oath_relay.otp import OtpError, counter_for, hotp, normalize_secret, totp

# RFC 4226 Appendix D: ASCII "12345678901234567890"
RFC4226_SECRET = "GEZDGNBVGY3TQOJQGEZDGNBVGY3TQOJQ"
RFC4226_CODES = [
    "755224",
    "287082",
    "359152",
    "969429",
    "338314",
    "254676",
    "287922",
    "162583",
    "399871",
    "520489",
]


@pytest.mark.parametrize(("counter", "expected"), list(enumerate(RFC4226_CODES)))
def test_rfc4226_vectors(counter: int, expected: str) -> None:
    assert hotp(RFC4226_SECRET, counter) == expected


@pytest.mark.parametrize(
    ("at", "expected"),
    [
        (59, "94287082"),
        (1111111109, "07081804"),
        (1111111111, "14050471"),
        (1234567890, "89005924"),
        (2000000000, "69279037"),
        (20000000000, "65353130"),
    ],
)
def test_rfc6238_sha1_vectors(at: int, expected: str) -> None:
    assert totp(RFC4226_SECRET, digits=8, at=at) == expected


def test_rfc6238_sha256_vector() -> None:
    # RFC 6238 Appendix B, SHA-256 seed is ASCII "12345678901234567890123456789012"
    secret = "GEZDGNBVGY3TQOJQGEZDGNBVGY3TQOJQGEZDGNBVGY3TQOJQGEZA"
    assert totp(secret, digits=8, algorithm="sha256", at=59) == "46119246"


def test_secret_normalization_is_lenient() -> None:
    expected = normalize_secret(RFC4226_SECRET)
    assert normalize_secret(RFC4226_SECRET.lower()) == expected
    assert normalize_secret(f"  {RFC4226_SECRET}  ") == expected
    assert normalize_secret(RFC4226_SECRET + "====") == expected
    # Duo hands out secrets whose base32 encoding ends in four '=' characters
    assert normalize_secret("MZXW6===") == normalize_secret("MZXW6")


def test_bytes_secret_is_accepted() -> None:
    assert hotp(normalize_secret(RFC4226_SECRET), 0) == RFC4226_CODES[0]


def test_counter_for_uses_period() -> None:
    assert counter_for(30, at=59) == 1
    assert counter_for(30, at=60) == 2
    assert counter_for(60, at=119) == 1


@pytest.mark.parametrize("secret", ["", "   ", "not base32!", "1111"])
def test_invalid_secret_raises(secret: str) -> None:
    with pytest.raises(OtpError):
        normalize_secret(secret)


@pytest.mark.parametrize("digits", [0, 11, -1])
def test_digits_out_of_range(digits: int) -> None:
    with pytest.raises(OtpError):
        hotp(RFC4226_SECRET, 0, digits=digits)


def test_unknown_algorithm_raises() -> None:
    with pytest.raises(OtpError):
        hotp(RFC4226_SECRET, 0, algorithm="md5")


@pytest.mark.parametrize("counter", [-1, 1 << 64])
def test_counter_out_of_range(counter: int) -> None:
    with pytest.raises(OtpError):
        hotp(RFC4226_SECRET, counter)


def test_digits_pad_with_leading_zeros() -> None:
    # counter 5 of the RFC vector set is 254676; find one that needs padding
    padded = [hotp(RFC4226_SECRET, c) for c in range(100)]
    assert any(code.startswith("0") for code in padded)
    assert all(len(code) == 6 for code in padded)


@pytest.mark.skipif(
    not os.environ.get("OATH_RELAY_DUO_SECRET"),
    reason="set OATH_RELAY_DUO_SECRET to check a real Duo enrolment secret",
)
def test_duo_enrolment_vectors() -> None:
    """A real Duo HOTP secret must reproduce the codes Duo accepts."""
    secret = os.environ["OATH_RELAY_DUO_SECRET"]
    expected = os.environ["OATH_RELAY_DUO_CODES"].split(",")
    assert [hotp(secret, c) for c in range(len(expected))] == expected
