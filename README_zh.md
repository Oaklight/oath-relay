# oath-relay

[![PyPI](https://img.shields.io/pypi/v/oath-relay.svg)](https://pypi.org/project/oath-relay/)
[![Python](https://img.shields.io/pypi/pyversions/oath-relay.svg)](https://pypi.org/project/oath-relay/)
[![License](https://img.shields.io/pypi/l/oath-relay.svg)](LICENSE)
[![Dependencies](https://img.shields.io/badge/runtime%20dependencies-none-brightgreen.svg)](pyproject.toml)

[English](README_en.md) | **中文**

**用单一计数器，让多台设备共用一个有状态的 HOTP 令牌。**

`oath-relay` 是一个零依赖的小服务：它持有若干 HOTP 令牌的计数器，通过 HTTP
发放下一个验证码。它存在的理由很简单 —— HOTP 是唯一无法像 TOTP 那样同步的
二次验证方案。

```
HOTP = HMAC-SHA1(secret, counter)
TOTP = HMAC-SHA1(secret, floor(unixtime / 30))
```

TOTP 是时钟的纯函数，任何设备用同一个 secret 都能算出相同的码。HOTP 则是
**可变计数器**的函数：两台设备各持一份 secret，会各自推进、永久分叉，而且
两个计数器无法合并。唯一正确的答案是有且只有一个写入者。

`oath-relay` 就是那个写入者。

## 为什么不能直接用密码管理器？

Bitwarden —— 以及本项目最初针对的 Vaultwarden —— 只支持 TOTP。把
`otpauth://hotp/...` 交给它，它会拿同一个 secret 当成 TOTP 来算，码永远对不上。
实测：

```
Bitwarden 从 otpauth://hotp/ 算出的码 : 332512   （等于该 secret 的 TOTP）
真实 HOTP 在 counter 1 的码           : 996554
```

有些服务只认 HOTP。例如 Cisco Duo 由租户决定：激活响应里有一个 `use_totp`
标志，为 `false` 时任何 TOTP 客户端都不可能成功。

## 安装

```bash
pipx install oath-relay          # 或：pip install oath-relay
```

需要 Python 3.11+。除标准库外没有任何运行时依赖。

## 快速开始

```bash
oath-relay init                     # 生成 ./oath-relay.toml（权限 0600）
$EDITOR oath-relay.toml             # 填入你的令牌
oath-relay hash                     # 生成 auth.basic_password_hash
oath-relay serve                    # http://127.0.0.1:8790
```

```bash
curl -X POST -H 'Authorization: Bearer <token>' \
     http://127.0.0.1:8790/api/tokens/my-token/next
# {"name":"my-token","type":"hotp","code":"755224","counter":0,"next_counter":1}
```

或者用浏览器打开 `http://127.0.0.1:8790/`，点按钮取码。

## 配置

静态配置（secret）放在 TOML 里；可变状态（计数器）放在单独的 JSON 文件里。
这样状态文件可以安全地备份和对比，不必带着 secret 到处走。

```toml
[server]
host = "127.0.0.1"
port = 8790
allow_peek = false

[state]
path = "./state/state.json"

[auth]
basic_user = "alice"
basic_password_hash = "scrypt$..."     # 由 `oath-relay hash` 生成
bearer_tokens = ["long-random-string"] # 可写多个，便于轮换

[[tokens]]
name = "example"
type = "hotp"          # 或 "totp"
secret = "JBSWY3DPEHPK3PXP"
digits = 6
algorithm = "sha1"     # sha1 | sha256 | sha512
counter_start = 0      # 仅在发出第一个码之前使用
```

环境变量覆盖格式为 `OATH_RELAY_<SECTION>__<KEY>`：

```bash
OATH_RELAY_SERVER__PORT=9000
OATH_RELAY_AUTH__BEARER_TOKENS=one,two
```

配置文件查找顺序：`./oath-relay.toml`、`/config/config.toml`、
`/etc/oath-relay/config.toml`。可用 `--config PATH` 指定。

## HTTP 接口

| 方法 | 路径 | 作用 |
| --- | --- | --- |
| GET | `/` | HTML 页面，每个令牌一个按钮 |
| GET | `/healthz` | `{"status":"ok"}`，无需认证 |
| GET | `/api/tokens` | 名称、类型、计数器；**不含 secret** |
| GET | `/api/tokens/{name}/status` | 当前计数器，只读 |
| POST | `/api/tokens/{name}/next` | **发放一个码并推进计数器** |
| POST | `/api/tokens/{name}/resync` | 强制设定计数器（恢复用） |
| GET | `/api/tokens/{name}/peek` | 不推进计数器取码（默认关闭） |

`POST .../next` 不接受请求体，返回：

```json
{"name": "example", "type": "hotp", "code": "755224", "counter": 0, "next_counter": 1}
```

TOTP 令牌返回的是 `remaining`（距离下一次滚动的秒数），且从不写状态文件。

`POST .../resync` 接受 `{"counter": 42}`，是计数器漂移后的恢复路径。

### GET 永不修改状态

这是刻意的设计，并有测试保证。浏览器预取、链接预览、爬虫都会发 GET；如果
GET 会推进计数器，漂移就会在无声中发生。只有上面两个 POST 接口会改变状态。

## 命令行

```bash
oath-relay serve [--host H] [--port P]   # 启动服务
oath-relay code NAME [-q]                # 打印下一个码，消耗计数器
oath-relay peek NAME [-q]                # 打印当前码，不消耗
oath-relay list                          # 列出令牌及其计数器
oath-relay status NAME                   # 只看计数器
oath-relay resync NAME COUNTER           # 强制设定计数器
oath-relay hash [-p PASSWORD]            # 生成 basic auth 的 scrypt 哈希
oath-relay init [PATH] [-f]              # 写出模板配置
```

`code` 与 `serve` 共用同一个状态文件，因此本地命令与 HTTP 请求由同一把
`flock` 串行化。

## Docker

```bash
docker build -t oath-relay:dev .
docker run --rm -p 127.0.0.1:8790:8790 \
  -v "$PWD/config.toml:/config/config.toml:ro" \
  -v "$PWD/state:/state" \
  oath-relay:dev
```

首次运行时若没有挂载配置，`docker/entrypoint.sh` 会写出一份模板。容器以非
root 用户（uid 10001）运行，`/state` 声明为卷。**从宿主机挂载的配置文件必须
对该 uid 可读**，否则容器启动即退出。

## 威胁模型

暴露服务之前请先读这一节。

- **bearer token 就等于第二因子。** 任何能调用 `POST .../next` 的人都能以你的
  身份登录。请像保护 secret 一样保护这个接口：只绑内网、token 要长、定期轮换。
- **关闭认证后，这个服务就是一台凭据打印机。** 启动时会打印警告。只有当主机
  只有你能访问时才可以接受。
- **`peek` 会抵消计数器的意义。** 它返回当前码而不推进，调试时有用，生产环境
  危险 —— 生成了却没使用的码，正是 HOTP 漂移的来源。默认关闭。
- **每个令牌只能有一个写入者。** 把所有设备都指向这个服务，不要再把同一个
  secret 导进手机认证器：两个写入者正是这个工具要避免的问题。
- **不记录验证码。** secret 和生成的码都不会写入标准输出或状态文件。

## 这个项目不是什么

- 不是身份提供方。它不认证用户，只发放验证码。
- 不是 TOTP 同步服务。TOTP 不需要服务端，本地算即可。
- 不是绕过他人二次验证的工具。请只用于你自己的账号。

## 开发

```bash
python -m venv .venv && . .venv/bin/activate
pip install -e '.[dev]'
pytest
ruff check src/ tests/ && ruff format --check src/ tests/
```

测试覆盖 RFC 4226 与 RFC 6238 的官方向量、跨进程并发递增，以及 HTTP 契约
（包括「GET 不修改状态」这一条）。

若要用真实注册的 secret 校验，运行 pytest 前设置 `OATH_RELAY_DUO_SECRET` 和
`OATH_RELAY_DUO_CODES`（逗号分隔，从 counter 0 开始）。未设置时该测试自动跳过，
因此 secret 不会进入仓库。

## 许可证

MIT，见 [LICENSE](LICENSE)。
