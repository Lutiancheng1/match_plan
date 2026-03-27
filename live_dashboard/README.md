# 实时比赛看板 (Live Dashboard)

本地 HTTP 服务，持续从源站抓取实时比赛数据（赔率、比分、盘口），在浏览器中以看板形式展示。

## 架构

```
start_live_dashboard.sh  →  serve_live_dashboard.py  →  浏览器 (127.0.0.1:8765)
                               ├── auto_login.py          (自动登录获取 cookie)
                               ├── poll_get_game_list.py   (抓取 + 解析 XML 数据)
                               └── db_store.py             (SQLite 历史存储)
```

- 后台每 N 秒（默认1秒）请求源站 `transform.php` 接口，解析返回的 XML
- 将结构化 JSON 写入 `live_service_data/latest.json`
- 前端每秒通过 `/api/latest.json` 拉取数据，仅在数据变化时重绘页面
- 检测到 `doubleLogin` 会立即自动重登，无需人工干预

## 文件说明

| 文件 | 职责 |
|------|------|
| `serve_live_dashboard.py` | HTTP 服务 + 前端 HTML + 主循环调度 |
| `poll_get_game_list.py` | 数据抓取、XML/JSON 解析、字段映射 |
| `auto_login.py` | 纯 Python 自动登录（仅标准库 urllib） |
| `db_store.py` | SQLite 历史快照存储，线程安全 |
| `start_live_dashboard.sh` | 启动脚本（读取 env → 启动 Python → 打开浏览器） |
| `stop_live_dashboard.sh` | 停止脚本（通过 PID 文件终止进程） |
| `Start Live Dashboard.command` | macOS 双击启动 |
| `Stop Live Dashboard.command` | macOS 双击停止 |
| `live_dashboard.env` | 运行配置（含登录凭据，不提交到版本控制） |
| `live_dashboard.env.example` | 配置模板 |
| `live_service_data/` | 运行时数据目录（自动创建） |

## 快速启动

### 1. 配置

```bash
cp live_dashboard.env.example live_dashboard.env
```

编辑 `live_dashboard.env`，填入登录凭据：

```env
# 自动登录（推荐）
LOGIN_USERNAME=你的用户名
LOGIN_PASSWORD=你的密码
ENTRY_URL=https://112.121.42.168
```

或使用手动 Cookie 模式（备用）：

```env
GET_GAME_LIST_COOKIE='protocolstr=aHR0cHM=; CookieChk=WQ; ...'
GET_GAME_LIST_BODY='uid=xxx&ver=2026-03-19-fireicon_142&...'
```

### 2. 启动

```bash
zsh start_live_dashboard.sh
```

或 macOS 双击 `Start Live Dashboard.command`。

### 3. 访问

```
http://127.0.0.1:8765
```

### 4. 停止

```bash
zsh stop_live_dashboard.sh
```

## 配置项

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `LOGIN_USERNAME` | — | 自动登录用户名（推荐） |
| `LOGIN_PASSWORD` | — | 自动登录密码 |
| `ENTRY_URL` | `https://112.121.42.168` | 源站入口 URL |
| `GET_GAME_LIST_COOKIE` | — | 手动 Cookie（自动登录时无需填） |
| `GET_GAME_LIST_BODY` | — | 手动 Body 模板（自动登录时无需填） |
| `HOST` | `127.0.0.1` | 监听地址 |
| `PORT` | `8765` | 监听端口 |
| `TITLE` | `全部比赛实时看板` | 页面标题 |
| `POLL_INTERVAL` | `1` | 后端抓取间隔（秒） |
| `REFRESH_MS` | `1000` | 前端刷新间隔（毫秒） |
| `TIMEOUT` | `30` | 网络请求超时（秒） |
| `INCLUDE_MORE` | `0` | 是否抓取足球全盘口明细（1=是） |
| `MORE_FILTER` | `All` | 盘口筛选（INCLUDE_MORE=1 时生效） |
| `GTYPES` | `ft,bm,tt,bs,sk` | 要抓取的运动类型（逗号分隔） |
| `DB_ENABLED` | `0` | 是否启用 SQLite 历史存储（1=是） |
| `DB_KEEP_HOURS` | `24` | 历史数据保留时长（小时） |
| `OUTPUT_DIR` | `./live_service_data` | 数据输出目录 |

### 推荐配置

**实时看列表**（低延迟）：
```env
POLL_INTERVAL=1
REFRESH_MS=1000
INCLUDE_MORE=0
```

**看足球全盘口**（数据更全但更慢）：
```env
POLL_INTERVAL=10
REFRESH_MS=1000
INCLUDE_MORE=1
MORE_FILTER=All
```

## 运行时文件

在 `live_service_data/` 目录下：

| 文件 | 说明 |
|------|------|
| `latest.json` | 最新一次抓取的完整数据 |
| `status.json` | 服务运行状态（上次抓取时间、错误信息、比赛数量） |
| `server.log` | 服务日志（含登录信息） |
| `server.pid` | 进程 PID |
| `history.db` | SQLite 历史快照数据库（DB_ENABLED=1 时生成） |

## API 端点

| 路由 | 说明 |
|------|------|
| `GET /` | 看板页面 |
| `GET /api/latest.json` | 最新抓取数据 |
| `GET /api/status.json` | 服务状态 |
| `GET /api/history?gid=xxx&limit=100` | 查询某场比赛的历史快照 |
| `GET /api/stats` | 数据库统计信息 |

## 自动登录流程

1. POST `detection=Y` 到入口 URL → 获取 `ver` 和 `iovationKey`
2. POST `chk_login`（transform_nl.php 端点）→ 获取 uid、mid、登录 cookie
3. POST `memSet action=check` → 跳过"简易密码设定"弹窗
4. 组装 cookie 字符串和 body 模板，供后续数据抓取使用

**关键细节**：
- 登录用 `transform_nl.php`，数据抓取用 `transform.php`（不同端点）
- iovation blackbox 发送空值即可（服务器不强制校验）
- ver 提取失败时使用 fallback 值
- 必须复用同一个 `urllib.OpenerDirector` 实例传递 cookie
- 检测到 `doubleLogin` 错误会立即触发重新登录
- 连续 5 次请求异常也会触发重新登录

## 源站接口

- 数据接口：`POST https://112.121.42.168/transform.php?ver={ver}`
- 登录接口：`POST https://112.121.42.168/transform_nl.php?ver={ver}`
- 响应格式：XML
- 运动类型代码：FT=足球, BM=羽毛球, TT=乒乓球, BS=棒球, SK=斯诺克, BK=篮球, ES=电竞, TN=网球, VB=排球, OP=其他
- showtype=live + rtype=rb 表示"滚球（进行中）"

## 依赖

- Python 3（仅标准库，无第三方依赖）
- macOS / Linux / Windows
- 有效的源站账号（自动登录）或有效的 Cookie（手动模式）

## 已知限制

- Cookie 会过期，自动登录会在检测到 doubleLogin 时重登
- 源站 HTTPS 证书不受信，代码中使用了 `ssl._create_unverified_context()`
- `INCLUDE_MORE=1` 时每个比赛额外请求一次盘口明细，间隔建议 >= 5 秒
