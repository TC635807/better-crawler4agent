# dsh-plugin — DeepSeek Harness 接入

把本仓库的真实浏览器抓取能力装进 **DeepSeek Harness（dsh）**。

本仓库本身是标准 stdio MCP server + skill，**不是 dsh 插件**：dsh 的插件是一个
声明了 `dsh.bundle.patch` 的包。这个目录就是那层适配——一个 bundle，通过
`@deepseek-ai/dsh-mcp-client` 把 `scripts/launch.py` 拉起的 MCP server 注册进
profile，模型即可获得三个工具：

| 工具名 | 作用 |
|---|---|
| `mcp__better-crawler__fetch_url` | 抓单个页面，返回干净正文 JSON |
| `mcp__better-crawler__fetch_urls` | 并发批量抓取（逐条独立成败） |
| `mcp__better-crawler__crawler_status` | 排查浏览器是否可用 |

同时把仓库的 `skills/web-to-text` 链接到 `$DSH_HOME/skills`，支持技能的会话会
直接发现 `better-crawler-4-agent`。

## 安装

```sh
./dsh-plugin/install.sh            # 装进 web profile（默认）
./dsh-plugin/install.sh tui        # 或指定 profile
```

脚本做三件事：把本目录**暂存**到 `$DSH_HOME/plugins/better-crawler-4-agent-dsh`
并写入真实绝对路径 → `dsh plugin --profile <profile> add file:<暂存目录>` →
把技能链接进 `$DSH_HOME/skills`。

> 为什么要暂存：stdio MCP 行必须写死启动器的**绝对路径**，而仓库可能 clone 在
> 任何位置。`cordis.patch.yml` 和 `scripts/launch.sh` 里的 `__PLUGIN_DIR__` /
> `__REPO_DIR__` 就是由 install.sh 替换的占位符，不是可用路径。所以**别把这个
> 目录直接 `dsh plugin add`**。

启用 HMR 时配置立即生效，无需重启；没启用时重启 dsh。

### 卸载

```sh
dsh plugin --profile web remove better-crawler-4-agent-dsh
rm -rf ~/.dsh/plugins/better-crawler-4-agent-dsh
rm -f ~/.dsh/skills/web-to-text          # 仅当它是本脚本建的链接
```

## 配置

patch 行 id 是 `mcp-better-crawler`，可被 profile 的 `cordis.patch.yml` 或
`--patch` 覆盖层按 id 关闭、改命令、调超时，不必改本目录。

| 位置 | 字段 | 默认 | 说明 |
|---|---|---|---|
| `config.env` | `BETTER_CRAWLER_TIMEOUT` | `25` | 单页抓取超时（秒），透传给 MCP server |
| `config.env` | `BETTER_CRAWLER_MAX_CHARS` | `50000` | 单次返回正文上限，避免一条结果吃光上下文 |
| `config` | `toolCallTimeoutMs` | `180000` | dsh 侧单次工具调用上限；`fetch_urls` 批量可能跑几十秒 |
| `config` | `serverName` | `better-crawler` | 工具名前缀 `mcp__<serverName>__<tool>` |

环境变量（由 `scripts/launch.sh` 读取）：

| 变量 | 作用 |
|---|---|
| `BETTER_CRAWLER_REPO` | 仓库根目录，覆盖自动推导 |
| `BETTER_CRAWLER_PYTHON` | 指定解释器绝对路径，优先级最高 |
| `BETTER_CRAWLER_BROWSERS_PATH` | 指定已有 Chromium 目录 |

## 解释器与浏览器是怎么找到的

`scripts/launch.sh` 依次尝试 `BETTER_CRAWLER_PYTHON` → 仓库 `.venv/bin/python`
→ 仓库 `.venv/Scripts/python.exe` → PATH 上的 `python3`；选中的是 `.exe` 时用
`wslpath -w` 把脚本路径转成 Windows 形式再 `exec`——Windows 解释器看不懂
`/mnt/d/...`，而 `launch.py` 自己的 re-exec 逻辑假设两个世界共用一套路径。
浏览器路径由仓库的 `launch.py` 解析（`PLAYWRIGHT_BROWSERS_PATH` → 仓库
`.playwright` → Playwright 默认目录）。

## 验证

```sh
dsh --profile web --dump-config | grep -A18 mcp-better-crawler
ps --ppid "$(pgrep -f 'dsh web' | head -1)" -o pid,etime,cmd   # 宿主是否拉起子进程
```

在会话里直接调 `mcp__better-crawler__crawler_status`，或给一个中文技术站链接看
模型是否自行使用。

## 已知限制

- 改了本目录后要重跑一次 `install.sh`，暂存副本才会更新（`launch.sh` 除外：
  它的路径在暂存时已写死，同样需要重跑）。
- 技能通过软链接提供，仓库目录被移动或删除会让技能失效。
- 抓取行为与合规约束（默认拒绝内网地址、遵守 robots.txt、不要高频批量）见
  `skills/web-to-text/SKILL.md`。
