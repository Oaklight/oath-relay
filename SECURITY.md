# Security Policy

`oath-relay` stores the secrets behind a second factor and hands out the codes
that satisfy it. A bug here is not a crash — it is someone logging in as you.
Please report accordingly.

## Supported versions

| Version | Supported |
| --- | --- |
| 0.2.x | Yes |
| < 0.2.0 | No — configuration format and dependency model changed |

Only the latest release receives fixes.

## Reporting a vulnerability

**Do not open a public issue.**

Use GitHub's private vulnerability reporting:

<https://github.com/Oaklight/oath-relay/security/advisories/new>

That channel is private between you and the maintainer until an advisory is
published.

Please include:

- Affected version (`oath-relay --version`) and how it runs (CLI, container)
- A description of the impact — what an attacker gains
- Steps to reproduce, with the config **redacted**
- Any suggested fix

**Never include** real secrets, live one-time codes, or a copy of a state file.
Replace every `secret` value with `<REDACTED>` before pasting a config.

## Scope

In scope:

- Authentication bypass on the HTTP API
- Reading another token's secret or counter
- Counter desynchronisation caused by the service itself (for example a code
  issued without the counter advancing, or the reverse)
- Secret or code leakage through logs, error messages, the HTML page, or the
  state file
- Path traversal, template injection, or request smuggling in the vendored
  HTTP server
- Denial of service that survives a restart

Out of scope:

- Misconfiguration. Running with authentication disabled on an interface
  others can reach is documented as unsafe and prints a warning at startup.
- Weakness of the provider's own 2FA. The service reproduces what the
  provider accepts; it does not make it stronger.
- Physical or root access to the host running the service. Anyone with that
  access can read the config and the state file.
- The inherent trade-off documented in the README's threat model: **an API
  that hands out second factors makes the bearer token equivalent to the
  second factor.** That is the design, not a vulnerability.
- Vulnerabilities in vendored `zerodep` modules that are not reachable from
  `oath-relay`'s code paths. Report those upstream at
  <https://github.com/Oaklight/zerodep>.

## If a secret may have leaked

Rotating a HOTP secret is not like rotating a password. In order:

1. Delete the device at the provider (Duo portal, or whichever service issued
   the enrolment).
2. Enrol again — this produces a new secret and restarts the counter at 0.
3. Update `config.jsonc`, and set `counter_start` to the value the provider
   now expects (usually 0).
4. Restart the service. If the old counter had advanced, the state file entry
   for that token is stale; `oath-relay resync <name> 0` fixes it.

Do not try to keep using the old secret with a corrected counter. Assume it is
compromised.

## Response

This is a personal project maintained on a best-effort basis. There is no bug
bounty and no guaranteed SLA. Expect an acknowledgement within about a week,
and a fix or mitigation as time allows. If you need a faster answer, say so in
the report and explain why.

## Hardening checklist for operators

- Bind to a private interface (tailnet, VPN, loopback) rather than `0.0.0.0`
- Keep authentication enabled; use long random bearer tokens and rotate them
- Leave `allow_peek` off
- Keep the config and state files at mode `0600`
- Back up `/state` — losing it desynchronises every token
- One writer per token: do not also import the same secret into a phone
  authenticator

---

## 安全策略（中文）

`oath-relay` 保存二次验证的密钥，并发放通过验证所需的验证码。这里的漏洞不是崩溃，
而是**别人能以你的身份登录**。

- **支持版本**：仅最新发布版（当前 0.2.x）。
- **报告方式**：使用 GitHub 私有漏洞报告
  <https://github.com/Oaklight/oath-relay/security/advisories/new>，**不要开公开 issue**。
- **不要提交**真实 secret、有效的验证码、状态文件副本。粘贴配置前请把每个 `secret`
  替换成 `<REDACTED>`。
- **不在范围内**：关闭认证后暴露在公网、提供方自身 2FA 的强度、主机 root 权限、
  以及 README 威胁模型中已说明的设计取舍 —— 发放第二因子的 API，其 bearer token
  在效果上等同于第二因子。
- **怀疑密钥泄露**：不要试图用修正后的计数器继续使用旧 secret。请在提供方处删除
  该设备并重新注册，然后更新配置与计数器。
