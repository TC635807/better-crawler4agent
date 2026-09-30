# dsh-plugin — DeepSeek Harness 接入

把本仓库的真实浏览器抓取能力装进 **DeepSeek Harness（dsh）**。

仓库根目录的 `package.json` 声明了 `dsh.bundle.patch`，所以**整个仓库就是一个 dsh 包**，
可以直接被 `dsh plugin add` 安装。bundle 通过 `@deepseek-ai/dsh-mcp-client` 把
`scripts/launch.py` 拉起的 stdio MCP server 注册进 profile，模型即可获得三个工具：

| 工具名 | 作用 |
|---|---|
| `mcp__better-crawler__fetch_url` | 抓单个页面，返回干净正文 JSON |
| `mcp__better-crawler__fetch_urls` | 并发批量抓取（逐条独立成败） |
| `mcp__better-crawler__crawler_status` | 排查浏览器是否可用 |

`install.sh` 还会把仓库的 `skills/web-to-text` 链接到 `$DSH_HOME/skills`，支持技能的
会话会直接发现 `better-crawler-4-agent`。

## 安装

```sh
# 从 GitHub 装（推荐）
dsh plugin --profile web add github:TC635807/better-crawler4agent

# 也可以钉 ref
dsh plugin --profile web add 'github:TC635807/better-crawler4agent#<sha>'
```

本地 clone 用脚本，效果是同一件事，外加发布技能：

```sh
./dsh-plugin/install.sh            # 装进 web profile（默认）
./dsh-plugin/install.sh tui        # 或指定 profile
```

`dsh-plugin/cordis.patch.yml` 里的启动器路径不是写死的，而是一个 `!!js` 表达式，
在 profile 启动时用 `ctx.get('profileContext').dir` 算出
`<profile>/node_modules/better-crawler-4-agent-dsh/dsh-plugin/scripts/launch.sh`。
所以装到哪都不用改文件，也没有安装期占位符替换。启用 HMR 时配置立即生效，无需重启；
没启用时重启 dsh。

### 先决条件：解释器与浏览器

包只带 Python 代码，不带 `.venv` 和 `.playwright`（两者都在 `.gitignore` 里），
所以装完还要让启动器找得到一个**具备依赖的解释器**：

```sh
# 在仓库里跑一次：建 venv、装依赖、下载 Chromium
python scripts/install.py
```

本地 clone 安装时，包内的 `.venv` 会随包一起被链接，装完即可用；从 GitHub 装时没有，
按下面的优先级给一个解释器即可：

```sh
# 1) 让它直接用另一个 clone（推荐：那个 clone 里已有 .venv 和 .playwright）
export BETTER_CRAWLER_REPO=/path/to/your/clone
# 2) 或直接指定解释器
export BETTER_CRAWLER_PYTHON=/path/to/clone/.venv/bin/python
```

`scripts/launch.sh` 的选择顺序：`BETTER_CRAWLER_PYTHON` → 包内
`.venv/bin/python`（Windows 是 `.venv/Scripts/python.exe`）→ PATH 上的 `python3`；
`launch.py` 自己还会在 `_ROOT/.venv` 里再找一次，并把 `.playwright` 作为浏览器目录的
第二顺位（第一顺位是 `PLAYWRIGHT_BROWSERS_PATH` / `BETTER_CRAWLER_BROWSERS_PATH`）。

### 卸载

```sh
dsh plugin --profile web remove better-crawler-4-agent-dsh
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

环境变量由 `dsh-plugin/scripts/launch.sh` 读取：

| 变量 | 作用 |
|---|---|
| `BETTER_CRAWLER_REPO` | 仓库根目录，覆盖自动推导 |
| `BETTER_CRAWLER_PYTHON` | 指定解释器绝对路径，优先级最高 |
| `BETTER_CRAWLER_BROWSERS_PATH` | 指定已有 Chromium 目录 |

覆盖方式（在 profile 的 `cordis.patch.yml` 里按 id 改，或加 `--patch` 覆盖层）：

```yaml
- id: mcp-better-crawler
  config:
    env:
      BETTER_CRAWLER_REPO: /home/me/better-crawler4agent
```

## 验证

```sh
dsh --profile web --dump-config | grep -A18 mcp-better-crawler
ps --ppid "$(pgrep -f 'dsh web' | head -1)" -o pid,etime,cmd   # 宿主是否拉起子进程
```

在会话里直接调 `mcp__better-crawler__crawler_status`，或给一个中文技术站链接看模型是否自行使用。

## 已知限制

- 从 GitHub / npm 装时拿到的是仓库文件，没有 `.venv` 和 `.playwright`，首次使用前需要
  一个具备依赖的解释器（见「先决条件」）。首次自举尚未自动化。
- 通过 `dsh plugin add` 安装不会自动发布技能，只装 bundle；技能链接由本目录的
  `install.sh` 建立，或手动把 `skills/web-to-text` 链进 `$DSH_HOME/skills`。
- 技能通过软链接提供，仓库目录被移动或删除会让技能失效。
- 抓取行为与合规约束（默认拒绝内网地址、遵守 robots.txt、不要高频批量）见
  `skills/web-to-text/SKILL.md`。
