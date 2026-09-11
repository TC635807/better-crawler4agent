# better-crawler-4-agent

把 URL 变成干净正文的 MCP server + skill。用真实浏览器渲染，所以知乎、CSDN、掘金、微信公众号、博客园这类"HTTP 直接抓回来是空壳"的站点也能拿到内容。

**职责边界**：只做 URL → 正文。URL 从哪来不在本工具范围内——搜索、用户给的链接、页面里的链接，都由调用方（Agent）自己决定。这样工具保持单一职责，也不需要维护搜索 API。

## 为什么不用内置抓取

大多数 Agent 自带的网页抓取是 HTTP 客户端 + 正文提取，对静态站够用，但：

- **JS 渲染的页面拿不到内容**——返回空壳或只有导航
- **反爬站点被拦**——知乎返回 403，CSDN 返回 521
- **正文提取退化**——页面上有内容，提取出来却是零散片段

本工具用真实 Chromium 渲染，并补了完整浏览器指纹（UA / Sec-Ch-Ua / Sec-Fetch-* 全套一致）与导航器覆盖，实测能稳定抓取上述站点。此外还处理了两个实际会踩的坑：知乎注入的**字体指纹蜜罐**（上万字符的重复串，纯按长度取会误选）和**假成功**（200 响应但实际是错误页/登录墙）。

## 安装

```bash
# 完整安装（建 venv、装依赖、下载 Chromium）
python scripts/install.py

# 已有 Playwright 浏览器，复用它，不重复下载 ~700MB
python scripts/install.py --browsers-path "C:\Users\you\AppData\Local\ms-playwright"

# 复用已有 Python 环境（比如项目里的 .venv）
python scripts/install.py --reuse-env "D:\some-project\.venv"

# 只装依赖，浏览器稍后自己配
python scripts/install.py --no-browser
```

安装脚本会把解释器绝对路径与浏览器目录写回 `.zcode-plugin/plugin.json`，避免"装了但客户端拉不起来"。

自检：

```bash
python scripts/selfcheck.py            # 依赖 + 浏览器 + 安全校验 + 真实抓取
python scripts/selfcheck.py --offline  # 不联网
```

## 作为 ZCode 插件使用

把仓库目录加入插件市场（Settings → Plugin Management → Discover → `+` → 本地目录），或直接在 `.zcode/config.json` 里声明 MCP server：

```json
{
  "mcp": {
    "servers": {
      "better-crawler": {
        "type": "stdio",
        "command": "D:\\better-crawler-4-agent\\.venv\\Scripts\\python.exe",
        "args": ["D:\\better-crawler-4-agent\\scripts\\launch.py"],
        "env": {
          "BETTER_CRAWLER_BROWSERS_PATH": "D:\\better-crawler-4-agent\\.playwright"
        }
      }
    }
  }
}
```

## 工具

| 工具 | 说明 |
|---|---|
| `fetch_url(url, max_chars)` | 抓单个页面，返回正文与元信息 |
| `fetch_urls(urls, max_chars, max_concurrent)` | 并发抓多个，逐条独立成败，最多 20 条 |
| `crawler_status()` | 查浏览器状态，排查启动失败 |

返回结构：

```json
{
  "url": "https://...",
  "ok": true,
  "title": "页面标题",
  "content": "正文……",
  "content_length": 13845,
  "tier": "browser",
  "elapsed": 3.0,
  "error": "",
  "truncated": false,
  "attempts": [{"tier": "browser", "ok": true, "chars": 13845}]
}
```

`tier` 说明命中哪条路径：`browser`（浏览器渲染）> `trafilatura`（静态快路径）> `httpx`（指纹兜底）。`content_length` 是截断前的真实长度；`truncated` 表示返回内容被 `max_chars` 截过。失败时 `attempts` 会记录每一层的具体原因，而不是只给一个"失败"。

## 抓取策略

分层降级，每层都记耗时与命中情况：

1. **浏览器渲染**（主路径）——共享单例 Chromium，信号量限并发，崩溃自动重建。冷启动约 2-7s，之后单页 2-5s
2. **trafilatura 直取**（快路径）——静态站约 1s。对反爬站会记录失败域名，10 分钟内跳过这层，省掉无谓试错
3. **httpx + 完整指纹**（兜底）——补 Sec-Ch-Ua / Sec-Fetch-* / HTTP2，CSDN 等站的 521 主要靠这套头解决

浏览器不可用时（未安装等）会自动降级为后两层，`crawler_status` 会给出原因。

## 配置

全部通过环境变量，都有合理默认值：

| 变量 | 默认 | 说明 |
|---|---|---|
| `BETTER_CRAWLER_PYTHON` | — | 指定解释器，覆盖自动探测 |
| `BETTER_CRAWLER_BROWSERS_PATH` | — | Chromium 所在目录 |
| `BETTER_CRAWLER_TIMEOUT` | `25` | 单页抓取超时（秒） |
| `BETTER_CRAWLER_MAX_CHARS` | `50000` | 默认正文返回上限 |
| `BETTER_CRAWLER_ALLOW_PRIVATE` | `0` | 设为 `1` 放开内网地址限制（仅本地调试） |
| `BETTER_CRAWLER_LOG_LEVEL` | `INFO` | 日志级别（输出到 stderr） |

## 安全

- 仅允许 `http` / `https`，拒绝 `file://`、`raw://` 等协议
- 解析目标主机名，拒绝回环、内网、链路本地、云元数据地址（`127.0.0.1`、`192.168.*`、`169.254.169.254` 等）
- 默认拒绝，本地调试需显式设 `BETTER_CRAWLER_ALLOW_PRIVATE=1`

注意：一个"抓任意 URL"的工具本质上是本地网络请求原语，请只在可信环境启用。

## 合规

抓取请遵守目标站点的 robots.txt 与服务条款。本工具适合按需读取具体页面，不要用于高频批量抓取。

## 结构

```
better-crawler-4-agent/
├── .zcode-plugin/plugin.json    # 插件清单（skills + mcpServers）
├── skills/web-to-text/SKILL.md  # 技能：何时用、如何选 URL、如何判断结果
├── src/better_crawler/
│   ├── browser.py               # 共享 Chromium 单例、指纹、崩溃重建
│   ├── extract.py               # 正文提取 + 蜜罐识别 + 错误页识别
│   ├── fetcher.py               # 分层编排
│   ├── safety.py                # SSRF 防护
│   └── mcp_server.py            # MCP 工具定义
├── scripts/
│   ├── launch.py                # 启动器：解析解释器与浏览器路径
│   ├── install.py               # 安装
│   └── selfcheck.py             # 自检
├── requirements.txt
└── pyproject.toml
```

## License

MIT
