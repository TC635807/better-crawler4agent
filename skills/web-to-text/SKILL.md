---
name: better-crawler-4-agent
description: Turn URLs into clean article text using a real browser, reliable on JS-heavy and anti-bot sites (Zhihu, CSDN, Juejin, WeChat, cnblogs) where plain HTTP fetching returns empty shells. Use when you need the full text of a specific web page, when a built-in fetch returns empty or truncated content, when crawling Chinese tech sites, or when the user gives you URLs to read. Also covers how to find good URLs with native tools and how to judge whether fetched content is trustworthy.
---

# better-crawler-4-agent · URL 到正文

把 URL 变成干净正文。用真实浏览器渲染，所以知乎、CSDN、掘金这类"HTTP 抓回来是空壳"的站点也能拿到内容。

**本技能的边界**：只负责"给我 URL，我还你正文"。URL 从哪来不在工具职责内——用你自己的搜索、用户给的链接、或页面里的链接，自行决定抓什么。

## 什么时候用它

| 场景 | 用不用 |
|---|---|
| 用户给了具体链接，要读内容 | **用** |
| 内置 fetch 返回空、报错、或明显截断 | **用** |
| 知乎/CSDN/掘金/公众号/博客园等中文站 | **用** |
| 需要 JS 渲染的页面（SPA、无限滚动首屏） | **用** |
| 只是想知道"有没有这个说法" | 不用，搜索摘要够了 |
| 需要登录才能看的内容 | 不用，本工具不带登录态 |

## 工具

### `fetch_url(url, max_chars)`

抓单个页面。返回 JSON：

```json
{
  "url": "...",
  "ok": true,
  "title": "页面标题",
  "content": "正文文本",
  "content_length": 13845,
  "status": 200,
  "tier": "browser",
  "elapsed": 3.0,
  "error": "",
  "truncated": false,
  "attempts": [{"tier": "browser", "ok": true, "chars": 13845}]
}
```

- `status` 是服务器返回的 HTTP 状态码，配合 `error` 判断失败原因（详见下文）
- `tier` 说明命中哪条路径：`browser`（浏览器渲染，最强）> `trafilatura`（静态站快路径）> `httpx`（指纹兜底）
- `ok: false` 时看 `error` 和 `attempts`，里面有每一层失败的具体原因
- `content_length` 是**截断前**的真实长度，`truncated` 表示返回的 `content` 被 `max_chars` 截过

### `fetch_urls(urls, max_chars, max_concurrent)`

批量抓，最多 20 条。**逐条独立成败**——一条失败不影响其他，返回里带 `ok_count`。适合"用户一次给了一堆链接"。

### `crawler_status()`

排查用。浏览器起不来时这里会给出原因。

## 怎么找 URL（这部分是你自己的活）

工具不搜索。URL 来源：

1. **用户直接给的链接** —— 最可靠，直接用
2. **你自己的搜索能力** —— 拿到候选后先扫一眼标题和 URL 结构
3. **页面里的链接** —— 已抓到的正文里若含目标链接，可继续抓

### 挑 URL 的几条经验

这几条能显著提高成功率，都是实战结论：

- **优先内容页**：`/article/details/xxx`、`/p/xxx`、`/post/xxx`、`/blog/xxx` 这类是正文页
- **避开聚合页**：`/tag/`、`/category/`、`/search`、`/author/`、`/user/` 通常是列表，没有正文
- **避开功能页**：`/login`、`/register`、`/download/`、`/video/`、商品页
- **同一站点别贪多**：一个域名抓 3-5 篇足够，再抓内容重复率高
- **注意 URL 去重**：`?locationNum=10`、`?utm_source=...` 这类参数不同的往往是同一页

## 判断结果是否可信

不要只看 `ok`。返回里的 `status` 和 `error` 能告诉你该重试还是该换 URL。

### 失败时怎么办

| `error` 内容 | 含义 | 行动 |
|---|---|---|
| `HTTP 404（页面不存在）` | URL 失效 | **换 URL**，别重试 |
| `HTTP 410（页面已永久移除）` | 内容已删除 | 换 URL |
| `HTTP 403（服务器拒绝访问）` | 触发反爬 | 可重试一次 |
| `HTTP 429（请求过于频繁）` | 被限流 | 降低频率后重试 |
| `HTTP 502 / 503 / 504` | 服务端异常 | 稍后重试 |
| `页面提示内容不存在或已被删除（服务端返回 200）` | **软 404**：状态码正常但页面是错误页 | 换 URL |
| `HTTP 200 但页面无有效正文` | 页面存在但无文字（纯图片/需登录） | 换 URL 或接受现状 |
| `域名无法解析（DNS 问题）` | 网络/DNS 环境问题 | 检查网络，或报告给用户 |
| `抓取超时（>N 秒）` | 页面太慢或卡住 | 可重试一次 |

**连续失败不要反复重试同一个 URL**——多数失败是 URL 本身无效，重试只是浪费时间。

### 成功时也要看一眼

- **`ok: true` 但 `content_length` 很小（< 500）** — 可能是图片为主的页面或登录墙，扫一眼 `content` 开头确认是不是正文
- **正文含"验证码""人机验证""安全验证"** — 被反爬拦了
- **正文是重复字符**（如 `mmmmmlli`）— 站点注入的字体指纹蜜罐，本工具已过滤，若仍出现请报告
- **`tier` 是 `httpx` 或 `trafilatura`** — 静态路径成功，但内容可能不如浏览器渲染完整

## 常见问题

**浏览器起不来**（`crawler_status` 显示 `unavailable`）
仍能用静态路径抓普通站点，但 JS 站点会失败。需要执行 `playwright install chromium`，或设置 `BETTER_CRAWLER_BROWSERS_PATH` 指向已有的浏览器目录。

**内容明显比网页上看到的少**
先确认是不是图片/视频为主的内容。若是长文被截断，加大 `max_chars`。

**某站点持续失败**
把域名和 `error` 内容报告给用户，不要反复重试同一个 URL——连续失败通常不是偶发。

## 合规

默认拒绝内网地址（`127.0.0.1`、`192.168.*`、`169.254.*` 等），只允许 http/https。需要本地调试时设 `BETTER_CRAWLER_ALLOW_PRIVATE=1`。

抓取请遵守目标站点的 robots.txt 与服务条款；不要用于高频批量抓取。
