# MatchPlan 录制系统

## 当前主线

**App 控制台 + Pion/GStreamer 直连接流**，不再依赖浏览器窗口录屏。

```
┌─────────────────────────────────────────────────────────┐
│  MatchPlanRecorderApp (SwiftUI)                         │
│                                                         │
│  ┌──────────────┐  ┌──────────────┐                     │
│  │ sftraders.live│  │ 数据站页面    │                     │
│  │  WKWebView   │  │  WKWebView   │                     │
│  └──────┬───────┘  └──────────────┘                     │
│         │                                               │
│         │ live 列表 + bootstrap                          │
└─────────┼───────────────────────────────────────────────┘
          │
          ▼
┌─── data_site_proxy (port 18780) ──────────────────────┐
│  - 反向代理数据站                                       │
│  - 单一 session 管理 (auto_login)                       │
│  - /credentials 端点: 共享 session 给后端               │
│  - /credentials/refresh: 强制重新登录                   │
└─────────┬─────────────────────────────────────────────┘
          │
          ▼
┌─── pion_gst_dispatcher ──────────────────────────────┐
│  - 从 proxy /credentials 获取 session (不自行登录)     │
│  - 发现直播 + 匹配盘口数据                              │
│  - 运行唯一的 BettingDataPoller (5s/次)                │
│  - 数据写入 shared_betting_data.jsonl                  │
│  - 为每场比赛分发 worker                                │
└─────────┬─────────────────────────────────────────────┘
          │ 每场比赛一个子进程
          ▼
┌─── run_pion_gst_direct_capture (worker) ──────────────┐
│  - SharedBettingDataReader 读取共享数据 (不独立请求)     │
│  - match_data_to_stream() 筛选本场数据                  │
│  - LiveTextPoller599 拉 599 文字直播做对齐辅助          │
│  - pion_gst_direct_chain (Go) 直连 LiveKit 接流        │
│  - 输出: 视频归档 + HLS 预览 + betting_data.jsonl       │
│          + live_events.jsonl (599)                     │
└───────────────────────────────────────────────────────┘
```

### 关键设计决策

1. **单一 session**: 只有 proxy 调用 `auto_login()`，所有组件通过 `/credentials` 复用，避免 doubleLogin
2. **集中数据采集**: dispatcher 统一 5s 请求一次数据站，worker 从共享文件读取，避免 N 个 worker 各自请求被封
3. **孤儿进程清理**: stop 时用 `pgrep -f` 兜底发现未被 dispatcher_state.json 追踪的进程

### 为什么不用浏览器录屏

旧方案 (`run_auto_capture.py`) 通过打开多个浏览器 watch 窗口 + ffmpeg 屏幕录制：
- 多路录制时被系统杀掉
- 依赖 PyObjC/AppleScript 控制窗口，不稳定
- 浏览器占大量内存和 CPU

新方案直接拿推流地址，用 Go + GStreamer 录制，稳定得多。

## 目录结构

### 主线入口

| 路径 | 说明 |
|---|---|
| `mac_app/MatchPlanRecorderApp/` | macOS 原生控制台 App |
| `pion_gst_direct_chain/` | Pion+GStreamer 录制后端 |

### 共享模块 (新旧方案都用)

| 文件 | 说明 |
|---|---|
| `auto_login.py` | 数据站自动登录 (只被 proxy 调用) |
| `poll_get_game_list.py` | 盘口数据轮询与 XML 解析 |
| `run_auto_capture.py` | BettingDataPoller 等共享类 (也是旧方案主脚本) |
| `material_filter_pipeline.py` | 素材过滤 (Gold/Silver/Reject) |
| `build_golden_sample_clips.py` | 从 Gold 素材切事件 clip |
| `backfill_timeline_csv.py` | 补生成 timeline.csv |

### 别名库

| 文件 | 说明 |
|---|---|
| `team_aliases.json` | 队名中英别名 |
| `league_aliases.json` | 联赛中英别名 |
| `team_alias_learned.json` | AI 翻译学习到的队名映射 |
| `league_alias_learned.json` | AI 翻译学习到的联赛映射 |

### 遗留 (新管线仍 import 其中共享类)

| 文件 | 说明 | 被谁引用 |
|---|---|---|
| `run_auto_capture.py` | BettingDataPoller, SessionLogger 等共享类 | pion_gst worker |
| `recorder.py` | Manifest 类 | pion_gst worker |
| `post_match.py` | merge_segments, get_video_duration | pion_gst worker |
| `notify_recording_summary.py` | Feishu 通知 | pion_gst dispatcher |

### 归档 (`_legacy/`)

旧浏览器录屏方案的脚本已归档到 `_legacy/` 目录，不再是主线。

## 运行模式

### 正式模式 (formalBoundOnly)

只录匹配到盘口数据的比赛，用于正式留档。

### 测试模式 (bestEffortAll)

不要求绑定盘口数据，用于验证稳定性和画质。

## Session 管理架构

### 为什么需要集中管理

数据站强制单 session：同一账号的新登录会使旧 session 失效 (doubleLogin)。如果多个组件各自调用 `auto_login()`，后登录的会踢掉先登录的。

### 当前方案

```
data_site_proxy.py (port 18780)
  ├── 启动时 pre-login: 提前准备好 session
  ├── GET /credentials: 返回当前缓存的 cookie/mid/uid/body_template
  ├── GET /credentials/refresh: 强制重新登录并返回新 session
  └── 所有数据站请求: 反向代理到上游，自动注入 cookie
```

**获取 session 的优先级** (`bootstrap_credentials()`):
1. proxy `/credentials` — 最优先，不会产生新登录
2. 共享凭证文件 (`MATCH_PLAN_SHARED_DATA_CREDENTIALS_FILE`) — 离线备选
3. dashboard 模式 — CDP 从浏览器拿
4. `auto_login()` — 最后手段，会使旧 session 失效

**session 失效时的恢复流程**:
1. 数据快照返回 0 行 → 检测到 session 可能失效
2. 先请求 proxy `/credentials/refresh` 重新登录
3. proxy 刷新失败才回退到本地 `auto_login()`

## 盘口数据采集架构

### 集中采集 (2026-03-31 改版)

之前每个 worker 独立轮询数据站，N 个 worker = N 倍请求量，有被封风险。

当前架构：
- **Dispatcher** 运行唯一的 `BettingDataPoller`，每 5s 请求一次
- 全量数据写入 `shared_betting_data.jsonl`（dispatcher runtime 目录下）
- **Worker** 使用 `SharedBettingDataReader` 从共享文件读取
- Worker 可选启动 `LiveTextPoller599`，实时拉 599 文字直播并做视频对齐辅助
- Worker 结束时调用 `match_data_to_stream()` 筛选出只属于自己比赛的数据
- 最终输出两个文件：
  - `raw_betting_data.jsonl` — 全部原始数据（备份）
  - `__betting_data.jsonl` — 筛选后的本场数据（正式使用）

## 599 对齐辅助链

### 当前目的

这条链不是替代盘口数据，而是解决“视频和数据到底有没有对齐”的问题。

当前做法：
- 录制时同步拉取 599 文字直播
- 用队名 / 联赛 / 开赛时间先匹配到 599 的 `thirdId`
- 用文字事件里的 `match_time` 反推比赛 kickoff
- 把 599 事件映射到本地视频时间轴
- 用本地 `betting_data` 的比分变化做补充交叉校验

### 当前实时产物

每场比赛目录内除了原来的 `__betting_data.jsonl` 外，新增：
- `__live_events.jsonl` — 599 文字直播事件流，实时写入

每个 session 根目录里会同步更新：
- `recording.log` — 包含 599 实时日志
- `worker_status.json` — `liveText599` 实时状态
- `session_result.json` — `live_text_599` 最终摘要

### 当前不会做的事

- 不会把 599 事件写进全局 `history.db`
- 不会改写原来的 `__betting_data.jsonl`
- 不会让 `__timeline.csv` 直接消费 599 事件

### 数据格式 (betting_data.jsonl)

```json
{"timestamp":"2026-03-24T06:00:01+00:00","gtype":"FT","gid":"12345",
 "team_h":"巴萨","team_c":"皇马","score_h":"2","score_c":"1",
 "fields":{"IOR_RMH":"1.85","RATIO_RE":"0.5","RUNNING":"Y",...}}
```

### 关键字段

| 字段 | 说明 |
|---|---|
| `SCORE_H` / `SCORE_C` | 主/客队比分 |
| `IOR_RMH` / `IOR_RMN` / `IOR_RMC` | 独赢赔率 (主/平/客) |
| `RATIO_RE` / `IOR_REH` / `IOR_REC` | 让球 (盘口/主/客) |
| `RATIO_ROUO` / `IOR_ROUH` / `IOR_ROUC` | 大小盘 (盘口/大/小) |
| `RUNNING` | 是否滚球 (Y/N) |
| `NOW_MODEL` | 比赛时间 (如 "2nd Half 65'") |

## 产物结构

每场录制一个 session 目录：

```
/Volumes/990 PRO PCIe 4T/match_plan_recordings/YYYY-MM-DD/
  session_pgstapp_.../
    recording.log
    session_result.json
    worker_status.json
    raw_betting_data.jsonl     # 全量原始数据 (备份)
    FT_TeamA_vs_TeamB_.../
      __betting_data.jsonl     # 筛选后的本场盘口数据
      __live_events.jsonl      # 599 文字直播事件 + 对齐注释
      __full.mp4               # 最终合并视频
      __timeline.csv           # 时间线
      __sync_viewer.html       # 同步回放页
      hls/playlist.m3u8        # HLS 预览
```

## 素材过滤

训练和评测只使用合格素材。三档分类：

- **Gold**: 视频+数据+时间线覆盖率 >= 95%，可直接用于训练
- **Silver Review**: 基本可用但覆盖率不足，需人工复核
- **Reject**: 无数据绑定或覆盖率太低

```bash
python3 material_filter_pipeline.py
```

## 凭证配置

`live_dashboard.env`:

```
LOGIN_USERNAME=<数据站用户名>
LOGIN_PASSWORD=<数据站密码>
ENTRY_URL=https://112.121.42.168
```

## 详细文档

- App: [mac_app/MatchPlanRecorderApp/README.md](mac_app/MatchPlanRecorderApp/README.md)
- 录制后端: [pion_gst_direct_chain/README.md](pion_gst_direct_chain/README.md)
- 代理配置: [../docs/proxy-and-network-setup.md](../docs/proxy-and-network-setup.md)
