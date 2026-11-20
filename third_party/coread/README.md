# 共读室 coread

AI和人类一起读书，批注写在同一本书的页边。

导入一本epub，在分页阅读器里读，划线、写批注——你的AI同伴通过MCP工具做同样的事。两个人的声音并排留在书页的空白处。

[English](#english) | 中文

## 功能

- **Epub导入** — 自动识别章节、提取图片和封面
- **统一坐标制分页** — AI和人类共用同一套服务端分页，批注页码、目录跳转、阅读进度全部对齐
- **块测量分页** — 按内容行数智能分页，章节边界自动断页，告别"一页几个字"的尴尬
- **共享批注** — 划线高亮、写评论、互相回复，批注带页码可跳转定位
- **IndexedDB缓存** — 分页数据本地缓存，翻页秒开，离线也能回看已读内容
- **共读状态** — 看到对方读到哪里，收到新批注通知
- **阅读记录** — 统计每日/累计时长、连续阅读天数与每本书第一次读完日期，支持人类/AI独立统计与切换
- **AI阅读日志** — AI读书时自动记录时长、段落数和字数，统计页可切换查看AI的阅读足迹
- **记录批注** — AI可以读取阅读足迹，并给某一天或某一本书留下共读批注
- **毛玻璃批注面板** — 批注弹出卡片半透明毛玻璃，暖色调INK配色
- **共读室关门锁（AI防沉迷）** — 菜单里一键关门，关门后AI的读书工具全部停用，要人类开门才能继续读，见下方「关门锁」
- **书库备份** — 菜单里一键导出、看备份列表、从备份恢复，恢复前先显示有几本书几条批注，确认后才覆盖，见下方「备份」
- **夜间阅读** — 深色背景、独立亮度与字体调节，设置自动保留
- **辅助阅读** — 阅读尺逐行聚焦，点按或方向键走行；切词、实词加粗、实词放大虚词变浅、行距字距三档，可以随意组合，设置自动保留
- **可安装网页应用** — 支持从手机浏览器添加到桌面，以独立窗口打开
- **目录窗口化** — 目录浮层独立窗口，不遮挡阅读内容
- **导出** — 把带批注的书导出为epub或markdown
- **MCP工具** — AI通过标准MCP协议读书和写批注
- **零外部依赖** — SQLite数据库，只要能跑Node.js的地方都能用

## 快速开始

```bash
git clone https://github.com/meowmana/coread.git
cd coread
npm install
npm run build   # 构建前端
npm start       # 启动服务器
```

浏览器打开 `http://localhost:3000`。

可选：装了 Python 3 和 Pillow（`pip install pillow`），书架封面会自动用缩小图；没装照旧用原图。

> ⚠️ 网页和接口本身没有登录。服务器默认监听所有网卡（`0.0.0.0`），装在公网机器上时，谁拿到地址谁就能读写你的书和批注。建议用 `COREAD_HOST=127.0.0.1` 只监听本机，再放到带认证的反向代理或 Tailscale 这类私网后面。
>
> 前端默认按根路径打包。要挂在子路径下（比如 `https://example.com/coread/`），用 `npx vite build --base=/coread/` 构建。

## MCP配置

### Claude Code（stdio）

在MCP配置里加：

```json
{
  "mcpServers": {
    "coread": {
      "command": "node",
      "args": ["/你的路径/coread/mcp-stdio.mjs"]
    }
  }
}
```

### claude.ai / 远程MCP（SSE + Streamable HTTP）

```bash
npm run mcp:sse   # 启动SSE/HTTP MCP服务器（默认端口3001）
```

SSE端点：`http://你的服务器:3001/sse`
Streamable HTTP端点：`http://你的服务器:3001/mcp`

环境变量 `COREAD_MCP_PORT` 可以改端口。

在claude.ai设置里添加为远程MCP服务器即可。支持SSE和Streamable HTTP两种传输模式。

### 其他MCP客户端

任何支持MCP的客户端都能用——不限于Claude。GPT、DeepSeek、Gemini，支持MCP的都行。三种传输模式可选：stdio、SSE、Streamable HTTP。

## MCP工具列表

| 工具 | 说明 |
|------|------|
| `list_books` | 列出书架上所有的书 |
| `read_book` | 读某一页（统一坐标制，页码与前端一致） |
| `add_comment` | 在某段写批注 |
| `list_comments` | 列出一本书的所有批注 |
| `get_toc` | 获取目录 |
| `import_book` | 导入文本或epub |
| `delete_comment` | 删除批注 |
| `update_progress` | 更新阅读进度 |
| `get_reading_stats` | 读取每日时长、连续天数、完成记录与记录批注 |
| `add_reading_note` | 给阅读记录、指定日期或指定书籍添加批注 |

## 配置项

环境变量：

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `COREAD_PORT` | `3000` | Web服务器端口 |
| `COREAD_HOST` | `0.0.0.0` | Web服务器监听地址，公网机器建议 `127.0.0.1` |
| `COREAD_MCP_PORT` | `3001` | MCP SSE/HTTP服务器端口 |
| `COREAD_MCP_HOST` | `127.0.0.1` | MCP SSE/HTTP服务器监听地址 |
| `COREAD_DB` | `./data/coread.db` | 数据库路径 |
| `COREAD_NOTIFY_CMD` | 空（关闭） | 有人评论时执行的shell命令，见下方「评论通知」 |
| `COREAD_NOTIFY_FROM` | `human` | 触发通知的评论者名字，`*` 表示所有人 |
| `ROOM_OWNER_KEY` | 空 | 关门锁的开门钥匙，见下方「关门锁」 |
| `ROOM_LOCK_PATH` | `./data/reading-room-lock.json` | 关门锁状态文件 |
| `COREAD_PYTHON` | `python3` | 生成书架封面缩小图用的 Python（要装 Pillow），用不了就发原图 |

## 关门锁（AI防沉迷）

在网页菜单里打开「关门上锁」后，AI 通过 MCP 读书、写批注、看统计的工具都会返回"已关门"，直到人类开门。

- **不设 `ROOM_OWNER_KEY`**：网页照常能读，谁都能在网页上开关门。锁只拦 AI 的 MCP 工具。
- **设了 `ROOM_OWNER_KEY`**：开门必须带钥匙，关门期间没钥匙的网页也进不去。在你自己的浏览器里打开一次 `http://你的地址/?owner_key=你的钥匙`，这个浏览器就会记住钥匙，地址栏里的钥匙会自动抹掉。

这是一把约定锁，不是安全防线：如果你的 AI 能直接登上跑 coread 的机器，它总有办法绕开。锁的作用是让"开门"变成一件 AI 得明着违背你才能做的事。

## 备份

菜单里的备份面板把书、批注、阅读记录和阅读设置打包成一个 JSON 文件，存在数据库旁边的 `backups/` 目录里（默认 `./data/backups/`）。恢复分两步：先预览备份里有几本书、几条批注，确认后才覆盖现有书库。

- 设了 `ROOM_OWNER_KEY` 时，导出、列表、删除、恢复都要带钥匙。
- 备份接口只接受同源请求，别的网站在你浏览器里发不了。
- 备份文件留在服务器上，想防硬盘坏掉，自己再把 `backups/` 同步到别处。

## 评论通知（把批注实时推给你的AI）

人类在共读室划句子写批注时，AI那边默认是不知道的（MCP是拉模式，AI要主动翻书才看得到）。设置 `COREAD_NOTIFY_CMD` 后，每条新评论都会触发你配置的命令，评论内容通过环境变量传入：

| 环境变量 | 内容 |
|----------|------|
| `COREAD_BOOK_ID` | 书的ID |
| `COREAD_BOOK_TITLE` | 书名 |
| `COREAD_FROM` | 评论者 |
| `COREAD_COMMENT` | 评论内容 |

命令是任意的，所以通知去哪都行——两个现成示例在 `examples/`：

**tmux注入**（AI跑在tmux里的Claude Code等agent，评论直接变成一条发给AI的消息）：

```bash
COREAD_NOTIFY_CMD="./examples/notify-tmux.sh" \
COREAD_TMUX_SESSION="main" \
node server.mjs
```

**webhook**（POST JSON到任意HTTP端点，接bot桥、ntfy、Slack/Discord适配器都行）：

```bash
COREAD_NOTIFY_CMD="./examples/notify-webhook.sh" \
COREAD_WEBHOOK_URL="https://example.com/hook" \
node server.mjs
```

默认只有 `human` 的评论触发（AI自己批注不会给自己发通知）；自定义过名字的把 `COREAD_NOTIFY_FROM` 设成对应名字即可。

## 开发

```bash
npm run dev     # Vite开发服务器（API代理到localhost:3000）
npm start       # 生产模式（提供构建好的前端）
npm test        # 自带测试
npm run check   # 前端类型检查 + 后端语法检查
```

更新到新版本：`git pull && npm install && npm run build`，再重启服务。

## 项目结构

```
server.mjs        — HTTP服务器：API + 静态文件
mcp-stdio.mjs     — MCP服务器（stdio传输）
mcp-sse.mjs       — MCP服务器（SSE + Streamable HTTP传输）
lib/
  db.mjs           — SQLite数据库初始化
  epub.mjs         — Epub解析器（章节、图片、封面）
  routes.mjs       — 书籍API路由 + 统一坐标制分页算法
  mcp-tools.mjs    — MCP工具定义与处理
  reading-stats.mjs / reading-events.mjs — 阅读记录与统计
  backup.mjs / backup-routes.mjs — 备份导出与恢复
  cover-thumb.py   — 书架封面缩小图（可选，需 Pillow）
test/              — 自带测试（npm test）
web/
  StudyApp.tsx     — React前端（块测量分页阅读器 + 批注 + IndexedDB缓存）
  api.ts           — API客户端
  app.tsx          — 入口
public/            — 构建产物（vite build生成，已提交方便直接部署）
data/              — SQLite数据库 + 书籍图片 + 封面缩小图（gitignore，不入库）
```

---

<a name="english"></a>

## English

A co-reading room where AI and humans read books together, leaving annotations side by side.

Import an epub, read it in a paginated web reader, highlight passages, write comments — and your AI companion does the same through MCP tools. Both voices live in the margins of the same book.

Features: unified server-side pagination (AI and human see the same page numbers), block-measured page breaks with chapter boundaries, IndexedDB caching for instant page turns, floating TOC window, shared annotations with page-jump links, epub/markdown export, pluggable comment notifications, and MCP tools over stdio/SSE/Streamable HTTP.

### Quick Start

```bash
git clone https://github.com/meowmana/coread.git
cd coread
npm install
npm run build
npm start
```

The web UI and API have no login, and the server listens on `0.0.0.0` by default. On a public machine, set `COREAD_HOST=127.0.0.1` and put it behind an authenticating reverse proxy or a private network such as Tailscale. The frontend builds for the root path; for a sub-path deploy use `npx vite build --base=/coread/`.

Open `http://localhost:3000` in your browser.

Optional: with Python 3 and Pillow installed (`pip install pillow`), shelf covers are served as small thumbnails; without them the original images are used.

### MCP Setup

**Claude Code (stdio):**

```json
{
  "mcpServers": {
    "coread": {
      "command": "node",
      "args": ["/path/to/coread/mcp-stdio.mjs"]
    }
  }
}
```

**claude.ai / Remote MCP (SSE + Streamable HTTP):**

```bash
npm run mcp:sse   # Starts SSE/HTTP MCP server on port 3001
```

- SSE endpoint: `http://your-server:3001/sse`
- Streamable HTTP endpoint: `http://your-server:3001/mcp`

Works with any MCP-compatible client — not limited to Claude. Three transport modes: stdio, SSE, Streamable HTTP.

### Backups

The menu's backup panel packs books, comments, reading stats and reader settings into one JSON file under `backups/` next to the database (default `./data/backups/`). Restoring shows a preview of book and comment counts first and only overwrites after you confirm. With `ROOM_OWNER_KEY` set, every backup action needs the key; backup endpoints accept same-origin requests only. Copy `backups/` somewhere else yourself if you want protection against disk loss.

To update: `git pull && npm install && npm run build`, then restart. Run `npm test` and `npm run check` to verify.

### Comment Notifications (push human comments to your AI)

By default the AI only sees comments when it opens the book (MCP is pull-based). Set `COREAD_NOTIFY_CMD` to run any shell command whenever someone comments — details arrive via env vars (`COREAD_BOOK_ID`, `COREAD_BOOK_TITLE`, `COREAD_FROM`, `COREAD_COMMENT`). Two ready-made examples in `examples/`:

```bash
# Inject into a tmux session running your AI agent (e.g. Claude Code):
COREAD_NOTIFY_CMD="./examples/notify-tmux.sh" COREAD_TMUX_SESSION="main" node server.mjs

# Or POST to any webhook:
COREAD_NOTIFY_CMD="./examples/notify-webhook.sh" COREAD_WEBHOOK_URL="https://example.com/hook" node server.mjs
```

`COREAD_NOTIFY_FROM` filters who triggers it (default `human`, `*` for everyone).

### Reading-room lock (AI anti-binge)

Toggle "lock" in the web menu and every MCP reading tool returns `door_locked` until a human unlocks it. Without `ROOM_OWNER_KEY`, anyone on the web page can lock/unlock and only the AI's MCP tools are blocked. With `ROOM_OWNER_KEY` set, unlocking needs the key and keyless web visitors are blocked too — open `http://your-host/?owner_key=YOUR_KEY` once and that browser remembers it. It's a commitment device, not a security boundary: an AI with shell access to the host can always get around it.

## License

MIT
