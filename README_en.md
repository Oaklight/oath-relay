# oath-relay

[![PyPI](https://img.shields.io/pypi/v/oath-relay.svg)](https://pypi.org/project/oath-relay/)
[![Python](https://img.shields.io/pypi/pyversions/oath-relay.svg)](https://pypi.org/project/oath-relay/)
[![License](https://img.shields.io/pypi/l/oath-relay.svg)](LICENSE)
[![Dependencies](https://img.shields.io/badge/runtime%20dependencies-none-brightgreen.svg)](pyproject.toml)

**English** | [中文](README_zh.md)

**Share a stateful HOTP token across devices from a single counter.**

`oath-relay` is a tiny, dependency-free service that owns the counter for one
or more HOTP tokens and hands out the next code over HTTP. It exists because
HOTP is the one 2FA scheme that cannot be synced the way TOTP can.

```
HOTP = HMAC-SHA1(secret, counter)
TOTP = HMAC-SHA1(secret, floor(unixtime / 30))
```

TOTP is a pure function of the clock, so every device computes the same code
from the same secret. HOTP is a function of a **mutable counter**: two clients
holding the same secret advance independently and diverge permanently. There
is no way to merge two counters. The only correct answer is one writer.

`oath-relay` is that writer.

## Why not just use a password manager?

Bitwarden — and Vaultwarden, which is what this was built against — supports
TOTP only. Handed an `otpauth://hotp/...` URI it silently computes a TOTP from
the same secret, so the codes never match. Measured:

```
Bitwarden from an otpauth://hotp/ URI : 332512   (== the TOTP for that secret)
Real HOTP at counter 1                : 996554
```

Some services are HOTP-only. Cisco Duo, for instance, decides per tenant via a
`use_totp` flag in the enrolment response; when it is `false`, no TOTP client
will ever work.

## Install

```bash
pipx install oath-relay          # or: pip install oath-relay
```

Python 3.10+. No runtime dependencies.

## Quick start

```bash
oath-relay init                     # writes ./oath-relay.jsonc (0600)
$EDITOR oath-relay.jsonc            # add your tokens
oath-relay hash                     # generate auth.basic_password_hash
oath-relay serve                    # http://127.0.0.1:8790
```

```bash
curl -X POST -H 'Authorization: Bearer <token>' \
     http://127.0.0.1:8790/api/tokens/my-token/next
# {"name":"my-token","type":"hotp","code":"755224","counter":0,"next_counter":1}
```

Or open `http://127.0.0.1:8790/` in a browser and press the button.

## Configuration

Static configuration (secrets) lives in JSONC — JSON with comments and
trailing commas. Mutable state (counters) lives in a separate file, so the
state file is safe to back up and diff.

```jsonc
{
  "server": { "host": "127.0.0.1", "port": 8790, "allow_peek": false },
  "state":  { "path": "./state/state.json" },

  "auth": {
    "basic_user": "alice",
    "basic_password_hash": "scrypt$...",     // from `oath-relay hash`
    "bearer_tokens": ["long-random-string"]  // several allowed, for rotation
  },

  "tokens": [
    {
      "name": "example",
      "type": "hotp",            // or "totp"
      "secret": "JBSWY3DPEHPK3PXP",
      "digits": 6,
      "algorithm": "sha1",       // sha1 | sha256 | sha512
      "counter_start": 0         // used only until the first code is issued
    }
  ]
}
```

Environment overrides use `OATH_RELAY_<SECTION>__<KEY>`:

```bash
OATH_RELAY_SERVER__PORT=9000
OATH_RELAY_AUTH__BEARER_TOKENS=one,two
```

Config search order: `./oath-relay.jsonc`, `/config/config.jsonc`,
`/etc/oath-relay/config.jsonc`. Override with `--config PATH`.

## HTTP API

| Method | Path | Effect |
| --- | --- | --- |
| GET | `/` | HTML page, one button per token |
| GET | `/healthz` | `{"status":"ok"}`, no auth |
| GET | `/api/tokens` | Names, types and counters; never secrets |
| GET | `/api/tokens/{name}/status` | Current counter, read-only |
| POST | `/api/tokens/{name}/next` | **Issues a code and advances the counter** |
| POST | `/api/tokens/{name}/resync` | Force a counter (recovery) |
| GET | `/api/tokens/{name}/peek` | Code without advancing (opt-in, see below) |

`POST .../next` accepts no body and returns:

```json
{"name": "example", "type": "hotp", "code": "755224", "counter": 0, "next_counter": 1}
```

TOTP tokens return `remaining` (seconds until rotation) instead of
`next_counter`, and never touch the state file.

`POST .../resync` takes `{"counter": 42}`. It is the recovery path for drift.

### GET never mutates

This is deliberate and enforced by tests. Browser prefetch, link previews and
crawlers all issue GETs; a counter that advances on a prefetch drifts silently.
Only the two POST endpoints above change state.

## CLI

```bash
oath-relay serve [--host H] [--port P]   # run the server
oath-relay code NAME [-q]                # print next code, consume counter
oath-relay peek NAME [-q]                # print current code, do not consume
oath-relay list                          # tokens and their counters
oath-relay status NAME                   # counter only
oath-relay resync NAME COUNTER           # force a counter
oath-relay hash [-p PASSWORD]            # scrypt hash for basic auth
oath-relay init [PATH] [-f]              # write a template config
```

`code` and `serve` share the same state file, so a local CLI call and an HTTP
call are serialised by the same `flock`.

## Docker

Published as `oaklight/oath-relay`.

```bash
docker run -d --name oath-relay --restart always \
  -p 127.0.0.1:8790:8790 \
  -e PUID=$(id -u) -e PGID=$(id -g) \
  -v "$PWD/config.jsonc:/config/config.jsonc:ro" \
  -v "$PWD/state:/state" \
  oaklight/oath-relay:latest
```

Or with Compose, which keeps the state volume explicit:

```bash
cp oath-relay.example.jsonc config.jsonc   # then edit it, chmod 600
docker compose up -d
```

**User and group.** The container starts as root only long enough to honour
`PUID`/`PGID`, then drops privileges, so the state file is owned by you rather
than by an arbitrary image uid. Set `PUID=$(id -u) PGID=$(id -g)`. Passing
`--user` instead also works: the drop is skipped and the process runs directly
as that user.

**Persistence.** `/state` holds the counters. It is declared as a volume and
bind-mounted to `./state` by `compose.yaml`. Losing it means every HOTP token
is out of sync with its provider — back it up like a credential.

A config mounted from the host must be readable by `PUID`.

## Threat model

Read this before exposing the service.

- **A bearer token is a second factor.** Anyone who can call
  `POST .../next` can log in as you. Protect the endpoint as carefully as the
  secret itself: bind it to a private network, keep tokens long, rotate them.
- **With auth disabled the service is a credential printer.** It warns at
  startup. Only acceptable on a host reachable by you alone.
- **`peek` defeats the purpose of a counter.** It returns the current code
  without advancing, which is useful for debugging and dangerous in
  production, because a code you generated but never used desynchronises you.
  It is off by default.
- **One writer per token.** Point every device at this service. Do not also
  import the same secret into a phone authenticator: two writers is exactly
  the divergence this tool exists to prevent.
- **Codes are not logged.** Neither the secret nor any generated code is
  written to stdout or to the state file.

## What this is not

- Not an identity provider. It does not authenticate users; it issues codes.
- Not a TOTP sync service. TOTP needs no server — compute it locally.
- Not a way to bypass someone else's 2FA. Use it for accounts you own.

## Development

```bash
python -m venv .venv && . .venv/bin/activate
pip install -e '.[dev]'
pytest
ruff check src/ tests/ && ruff format --check src/ tests/
```

The test suite covers the RFC 4226 and RFC 6238 vectors, concurrent counter
increments across processes, and the HTTP contract (including that GET never
mutates).

To check a real enrolment secret, set `OATH_RELAY_DUO_SECRET` and
`OATH_RELAY_DUO_CODES` (comma separated, starting at counter 0) before running
pytest. The test is skipped when they are unset, so secrets never land in the
repository.

## Security

This service holds second-factor secrets. See [SECURITY.md](SECURITY.md) for
the threat model, what is in and out of scope, and how to report a
vulnerability privately. Do not open a public issue for security problems.

## License

MIT. See [LICENSE](LICENSE).
