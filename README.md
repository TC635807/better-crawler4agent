# better-crawler-4-agent

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Python 3.10+](https://img.shields.io/badge/python-3.10%2B-blue.svg)](https://www.python.org/downloads/)
[![MCP](https://img.shields.io/badge/MCP-compatible-8A2BE2.svg)](https://modelcontextprotocol.io)
[![Playwright](https://img.shields.io/badge/Playwright-1.61-2EAD33.svg)](https://playwright.dev)

> 把 URL 变成干净正文的 MCP server。用真实浏览器渲染，知乎、CSDN、掘金这些"HTTP 抓回来是空壳"的站点也能拿到内容。

一个标准 stdio MCP server，**不绑定任何客户端**——Claude Code、Cursor、Windsurf、Cline、Codex、ZCode 都能用。

<details>
<summary><b>English summary</b></summary>

A vendor-neutral MCP server that turns URLs into clean article text using a real headless Chromium. Built for sites where plain HTTP fetching fails: JS-rendered pages and Chinese anti-bot sites like Zhihu, CSDN, and Juejin.

**Scope:** URL → text only. URL discovery is left to the caller, so the tool stays single-purpose and needs no search API keys.

**Key features:** real browser rendering, tiered fallback (browser → trafilatura → httpx+full browser fingerprint), font honeypot detection, fake-success detection (200 responses that are actually error pages), SSRF protection, and per-tier diagnostics in every response.

</details>

---

## 为什么需要它

大多数 Agent 自带的网页抓取是 HTTP 客户端 + 正文提取。对静态站点够用，但遇到下面三种情况会失败：

| 问题 | 表现 |
|---|---|
| **JS 渲染** | 返回空壳页面，只有导航和页脚 |
| **反爬拦截** | 知乎返回 403，CSDN 返回 521 |
| **提取退化** | 页面上有内容，提取出来却是零散片段 |

本项目用真实 Chromium 渲染，并补齐完整浏览器指纹（UA / Sec-Ch-Ua / Sec-Fetch-* 全套一致）+ 导航器覆盖。

### 实测对比

同一组 6 个 URL，开浏览器 vs 关浏览器（本项目 `Fetcher`，2026-09 实测）：

| | 成功率 | 知乎 | CSDN 字符数 | 博客园字符数 |
|---|---|---|---|---|
| **有浏览器** | **6/6 = 100%** | 27069 | 57218 | 15360 |
| 无浏览器（仅静态路径） | 5/6 = 83% | **失败** | 13845（−76%） | 4627（−70%） |

反爬站点直接失败，其他站点正文完整度也大幅下降——没有 JS 渲染只能拿到首屏片段。

### 已实测可抓取的站点

知乎专栏、CSDN、博客园、掘金、豆瓣读书、少数派、腾讯云社区、React 官方文档、Python 官方文档等。单页耗时 2–5 秒（浏览器暖启动后）。

---

## 快速开始

```bash
git clone git@github.com:TC635807/better-crawler4agent.git
cd better-crawler4agent
python scripts/install.py
```

`install.py` 会自动：

1. 在仓库内建 `.venv` 并安装依赖
2. 查找并复用系统中已有的 Playwright 浏览器（核对版本，避免重复下载 ~700MB）
3. **打印各 agent 的接入配置**，复制粘贴即可

验证安装：

```bash
python scripts/selfcheck.py            # 依赖 + 浏览器 + 安全校验 + 真实抓取
python scripts/selfcheck.py --offline  # 不联网
```

### 安装选项

```bash
python scripts/install.py --print-config       # 只看配置，不安装
python scripts/install.py --reuse-env <path>   # 复用已有 Python 环境
python scripts/install.py --browsers-path <p>  # 指定已有浏览器目录
python scripts/install.py --no-browser         # 稍后自己配浏览器
```

---

## 接入你的 Agent

### 通用 `mcpServers` 格式

Claude Code / Cursor / Windsurf / Cline 等通用：

```json
{
  "mcpServers": {
    "better-crawler": {
      "command": "D:\\better-crawler4agent\\.venv\\Scripts\\python.exe",
      "args": ["D:\\better-crawler4agent\\scripts\\launch.py"],
      "env": {
        "BETTER_CRAWLER_BROWSERS_PATH": "D:\\better-crawler4agent\\.playwright"
      }
    }
  }
}
```

> 路径用 `python scripts/install.py --print-config` 生成，它会填好你机器上的绝对路径。

配置放置位置：

| 客户端 | 位置 |
|---|---|
| Claude Code | 项目级 `<项目>/.mcp.json`，或用户级 `~/.claude.json` |
| Cursor | `~/.cursor/mcp.json` |
| Windsurf / Cline | 各自的 MCP 设置里粘贴 JSON |
| ZCode | `~/.zcode/cli/config.json` 的 `mcp.servers` 字段 |
| Codex | `~/.codex/config.toml` → `[mcp_servers.better-crawler]` |

仓库根目录带了一份 `.mcp.json`（相对路径），可直接引用。

### 不用 MCP，直接当库调用

```python
import sys
sys.path.insert(0, r"D:\better-crawler4agent\src")

from better_crawler import Fetcher

async def main():
    f = Fetcher()
    await f.start()                                  # 预热浏览器
    result = await f.fetch("https://example.com/article")
    if result.ok:
        print(result.content)
        print(f"命中层级: {result.tier}, {result.content_length} 字符")
    else:
        print(f"失败: {result.error}")
    await f.close()
```

`install.py --print-config` 会打印适合你环境的这段代码。

---

## 工具

| 工具 | 说明 |
|---|---|
| `fetch_url(url, max_chars)` | 抓单个页面 |
| `fetch_urls(urls, max_chars, max_concurrent)` | 并发抓取，逐条独立成败，最多 20 条 |
| `crawler_status()` | 查浏览器状态，排查启动失败 |

### 返回结构

```json
{
  "url": "https://...",
  "ok": true,
  "title": "页面标题",
  "content": "正文……",
  "content_length": 13845,
  "status": 200,
  "tier": "browser",
  "elapsed": 3.0,
  "error": "",
  "truncated": false,
  "attempts": [{"tier": "browser", "ok": true, "chars": 13845}]
}
```

设计要点是**不只返回成功/失败**：

- `status` — 服务器返回的 HTTP 状态码，用来区分"页面不存在"（404）与"抓取被拦"（403/429）
- `tier` — 命中哪条路径：`browser` > `trafilatura` > `httpx`
- `content_length` — 截断前的真实长度（`truncated` 表示返回内容被 `max_chars` 截过）
- `attempts` — 每一层的具体结果与失败原因

失败时 `error` 会给出可操作的原因，而不是笼统的"抓取失败"：

| error 内容 | 含义 | 该怎么做 |
|---|---|---|
| `服务器返回 HTTP 404（页面不存在）` | URL 失效 | 换 URL |
| `服务器返回 HTTP 403（服务器拒绝访问（可能触发反爬））` | 被拦截 | 可重试 |
| `服务器返回 HTTP 502（网关错误（服务端异常））` | 服务端异常 | 稍后重试 |
| `页面提示内容不存在或已被删除（服务端返回 200）` | **软 404**：状态码正常但页面是错误页 | 换 URL |
| `服务器返回 HTTP 200 但页面无有效正文` | 页面存在但没有正文（如纯图片/需登录） | 视情况 |
| `域名无法解析（DNS 问题）` | 网络环境问题 | 检查 DNS/网络 |

最后两类值得注意：站点可能用 **200 状态码**渲染"页面不存在"，或者页面确实没有文字内容。只看 HTTP 状态码会误判成成功，所以本工具会额外识别页面级错误特征。

---

## 抓取策略

分层降级，每层记录耗时与命中情况：

```
1. 浏览器渲染（主路径）
   共享单例 Chromium + 信号量限并发 + 崩溃自动重建
   冷启动 2–7s，之后单页 2–5s

2. trafilatura 直取（快路径）
   静态站约 1s；反爬站记录失败域名，10 分钟内跳过这层

3. httpx + 完整浏览器指纹（兜底）
   补 Sec-Ch-Ua / Sec-Fetch-* / HTTP2，解决 CSDN 等站的 521
```

浏览器不可用时（未安装等）自动降级为后两层，`crawler_status` 会给出原因。

### 工程细节

这些是踩坑换来的，改动前请先读代码注释：

- **单例浏览器 + 信号量** — 每 URL 起一个 Chromium 会迅速吃满内存
- **`asyncio.shield` + `done_callback`** — 超时取消会让 Playwright 内部 future 异常泄漏（`TargetClosedError: future exception was never retrieved`）
- **崩溃检测 + 单例重置** — Chromium 挂掉后必须重建，否则后续全部失败
- **字体蜜罐识别** — 知乎会注入上万字符的 `mmmmlli` 重复串，纯按长度取正文会误选它
- **假成功识别** — 200 响应可能是错误页、登录墙或文章已删除（知乎会渲染一张沙漠插画）
- **错误原因分类** — HTTP 状态码与 Chromium 网络错误映射成可读说明，让调用方能判断该重试还是换 URL
- **`--headless=new`** — 缺了它，部分站点只返回空壳

---

## 配置

全部通过环境变量，都有合理默认值：

| 变量 | 默认 | 说明 |
|---|---|---|
| `BETTER_CRAWLER_PYTHON` | — | 指定解释器，覆盖自动探测 |
| `BETTER_CRAWLER_BROWSERS_PATH` | — | Chromium 所在目录 |
| `BETTER_CRAWLER_TIMEOUT` | `25` | 单页抓取超时（秒） |
| `BETTER_CRAWLER_MAX_CHARS` | `50000` | 默认正文返回上限 |
| `BETTER_CRAWLER_ALLOW_PRIVATE` | `0` | 设为 `1` 放开内网限制（仅本地调试） |
| `BETTER_CRAWLER_LOG_LEVEL` | `INFO` | 日志级别（输出到 stderr） |

### 关于 Playwright 版本

`requirements.txt` **锁定** `playwright==1.61.0`。原因是 playwright 与浏览器 revision 强绑定（1.61 → rev 1228，1.62 → rev 1234），放宽版本会让"复用已有浏览器"因 revision 不匹配而静默失败。

升级 playwright 时必须同步重装浏览器：

```bash
python -m playwright install chromium
```

---

## 安全

- 仅允许 `http` / `https`，拒绝 `file://`、`raw://` 等协议
- 解析目标主机名，拒绝回环、内网、链路本地、云元数据地址（`127.0.0.1`、`192.168.*`、`169.254.169.254` 等）
- 默认拒绝，本地调试需显式设 `BETTER_CRAWLER_ALLOW_PRIVATE=1`

> 一个"抓任意 URL"的工具本质上是本地网络请求原语。请只在可信环境启用。

---

## 项目结构

```
better-crawler4agent/
├── .mcp.json                    # 通用 MCP 配置（相对路径）
├── skills/web-to-text/SKILL.md  # 技能：何时用、如何选 URL、如何判断结果
├── src/better_crawler/
│   ├── browser.py               # 共享 Chromium 单例、指纹、崩溃重建
│   ├── extract.py               # 正文提取 + 蜜罐识别 + 错误页识别
│   ├── fetcher.py               # 分层编排
│   ├── errors.py                # HTTP 状态码与网络错误 → 可读说明
│   ├── safety.py                # SSRF 防护
│   └── mcp_server.py            # MCP 工具定义
├── scripts/
│   ├── launch.py                # 启动器：解析解释器与浏览器路径
│   ├── install.py               # 安装 + 打印各 agent 配置
│   └── selfcheck.py             # 自检
└── tests/test_core.py           # 单元测试（不联网）
```

### 关于 skill

`skills/web-to-text/SKILL.md` 是纯 Markdown，与厂商无关，内容涵盖：何时该抓取、如何挑选高质量 URL（避开 `/tag/` `/user/` `/video/` 等聚合页）、如何判断结果是否可信。

支持 skill 的 agent（Claude Code、ZCode 等）可直接复制目录；不支持的，可把内容并入系统提示词。

---

## 开发

```bash
python -m pytest tests/ -q        # 单元测试（29 项，不联网）
python scripts/selfcheck.py       # 端到端自检（15 项，含真实抓取）
```

单元测试覆盖 SSRF 防护、蜜罐识别、正文提取、错误页识别。自检额外验证依赖可用性、浏览器启动与真实抓取（`--offline` 跳过抓取，为 12 项）。

---

## 合规

抓取请遵守目标站点的 `robots.txt` 与服务条款。本工具适合按需读取具体页面，**不要用于高频批量抓取**。

---

## License

[MIT](LICENSE)
