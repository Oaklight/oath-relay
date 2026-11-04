# oath-relay

Share a stateful HOTP token across devices from a single counter.

## The problem

TOTP is a pure function of the clock, so any number of devices can compute the
same code from the same secret. HOTP is a function of a **mutable counter**:
two clients holding the same secret advance independently and diverge
permanently, and there is no way to merge two counters.

Password managers that only implement TOTP — Bitwarden and Vaultwarden among
them — cannot help. Given an `otpauth://hotp/...` URI they compute a TOTP from
the same secret, so the codes never match.

## The shape of the answer

One process owns the counter. Every device asks it for the next code. The
counter advances exactly once per issued code, under an exclusive `flock`, and
is written atomically.

- [Configuration](configuration.md) — every key, with defaults
- [README](../README.md) — quick start, HTTP API, CLI, threat model

## Design decisions worth knowing

**GET never mutates.** Only `POST /api/tokens/{name}/next` and
`POST /api/tokens/{name}/resync` change state. Browser prefetch, link previews
and crawlers all issue GETs; a counter that advances on a prefetch drifts
silently. This is enforced by tests.

**Secrets and counters live in different files.** The config holds secrets and
is static; the state file holds counters and is mutable. That makes the state
file safe to back up and diff without moving secrets around.

**A removed token keeps its counter.** Deleting a `[[tokens]]` block from the
config does not reset its state, so a typo cannot silently rewind a counter to
zero and desynchronise you.

**`peek` is off by default.** Returning the current code without advancing is
useful when debugging and dangerous in production: a code generated but never
used is exactly how HOTP desynchronises.
