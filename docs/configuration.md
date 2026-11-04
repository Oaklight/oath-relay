# Configuration reference

`oath-relay` reads TOML from `--config PATH`, or the first of
`./oath-relay.toml`, `/config/config.toml`, `/etc/oath-relay/config.toml`.
Environment variables override file values.

## `[server]`

| Key | Type | Default | Meaning |
| --- | --- | --- | --- |
| `host` | string | `127.0.0.1` | Bind address. Use `0.0.0.0` inside a container. |
| `port` | int | `8790` | Bind port. |
| `allow_peek` | bool | `false` | Enable `GET /api/tokens/{name}/peek`, which returns a code without advancing the counter. |

## `[state]`

| Key | Type | Default | Meaning |
| --- | --- | --- | --- |
| `path` | string | `./state/state.json` | Counter file. Created 0600; its directory is created 0700. |

The state file is a JSON object keyed by token name:

```json
{"example": {"counter": 4, "updated_at": "2026-11-04T03:20:29Z"}}
```

## `[auth]`

| Key | Type | Default | Meaning |
| --- | --- | --- | --- |
| `basic_user` | string | `""` | Username for HTTP Basic auth. |
| `basic_password_hash` | string | `""` | `scrypt$...` from `oath-relay hash`. |
| `bearer_tokens` | array | `[]` | Accepted `Authorization: Bearer` values. Several allowed, for rotation. |

Basic auth is active only when both `basic_user` and `basic_password_hash` are
set. Bearer auth is active when the list is non-empty. With both inactive the
service is unauthenticated and prints a warning at startup — only acceptable on
a host reachable by you alone. `/healthz` is always unauthenticated.

## `[[tokens]]`

Repeat the block for each token.

| Key | Type | Default | Meaning |
| --- | --- | --- | --- |
| `name` | string | required | Identifier used in URLs. Letters, digits, `_`, `-`, `.`. |
| `type` | string | `hotp` | `hotp` or `totp`. |
| `secret` | string | required | Base32. Whitespace, case and padding are normalised. |
| `digits` | int | `6` | 1–10. |
| `algorithm` | string | `sha1` | `sha1`, `sha256` or `sha512`. |
| `period` | int | `30` | TOTP only. |
| `counter_start` | int | `0` | HOTP only. Used until the first code is issued. |
| `label` | string | `""` | Display name in the HTML page. |

## Environment overrides

`OATH_RELAY_<SECTION>__<KEY>`, for example:

```bash
OATH_RELAY_SERVER__PORT=9000
OATH_RELAY_STATE__PATH=/state/state.json
OATH_RELAY_AUTH__BASIC_USER=alice
OATH_RELAY_AUTH__BEARER_TOKENS=one,two
```

## Recovering from drift

If the client counter ran ahead of the server's, every generated code is
rejected and the client cannot recover on its own.

```bash
oath-relay status my-token              # what this service believes
oath-relay resync my-token 12           # force it
```

Or over HTTP:

```bash
curl -X POST -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
     -d '{"counter": 12}' http://127.0.0.1:8790/api/tokens/my-token/resync
```

If the true server-side counter is unknown, the reliable escape is to delete
the device at the provider and enrol again, which restarts at zero with a new
secret.
