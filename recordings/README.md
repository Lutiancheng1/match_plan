# MatchPlan 录制系统

## 当前主线

**App 控制台 + Pion/GStreamer 直连接流**，不再依赖浏览器窗口录屏。

```
App (WKWebView 登录 sftraders.live)
  |
  v
pion_gst_dispatcher → 发现直播 + 匹配盘口数据
  |
  v
run_pion_gst_direct_capture → 每场比赛独立 worker
  |
  v (直连 LiveKit 房间, 不经过浏览器)
pion_gst_direct_chain (Go) → 视频归档 + HLS 预览
  +
BettingDataPoller → 盘口数据轮询 → betting_data.jsonl
```

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
| `auto_login.py` | 数据站自动登录 |
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

### 遗留 (不再是主线)

| 文件 | 说明 |
|---|---|
| `run_auto_capture.py` | 旧方案主脚本 (浏览器录屏) |
| `recorder.py` | ffmpeg 多路录制核心 |
| `openclaw_recording_watch.py` | 旧的自动巡检调度 |
| `recording_watch_supervisor.py` | 旧的巡检守护层 |
| `window_capture.swift` | ScreenCaptureKit 窗口捕获 |

## 运行模式

### 正式模式 (formalBoundOnly)

只录匹配到盘口数据的比赛，用于正式留档。

### 测试模式 (bestEffortAll)

不要求绑定盘口数据，用于验证稳定性和画质。

## 产物结构

每场录制一个 session 目录：

```
/Volumes/990 PRO PCIe 4T/match_plan_recordings/YYYY-MM-DD/
  session_pgstapp_.../
    recording.log
    session_result.json
    worker_status.json
    FT_TeamA_vs_TeamB_.../
      __betting_data.jsonl     # 盘口数据
      __full.mp4               # 最终合并视频
      __timeline.csv           # 时间线
      __sync_viewer.html       # 同步回放页
      hls/playlist.m3u8        # HLS 预览
```

## 盘口数据

### 轮询架构

一条共享轮询链服务多场比赛，不是每场独立打 API。

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
