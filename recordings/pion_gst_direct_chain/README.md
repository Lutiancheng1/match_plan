# Pion + GStreamer 主录制链

这是当前正式采用的录制后端。

## 架构概览

```
pion_gst_supervisor.py          # 保活与控制入口 (start/stop/status)
  └── pion_gst_dispatcher.py    # 多流调度 + 集中数据采集
        ├── BettingDataPoller   # 唯一的数据轮询 (5s/次)
        │   └── shared_betting_data.jsonl  # 全量数据共享文件
        │
        ├── worker: run_pion_gst_direct_capture.py  # 比赛 A
        │     ├── SharedBettingDataReader  # 读共享文件，不独立请求
        │     └── main.go (pion_gst_direct_chain)  # Go 接流
        │
        ├── worker: run_pion_gst_direct_capture.py  # 比赛 B
        └── ...
```

## 组件说明

### main.go — Go 录制器
- 负责接流、主视频归档、HLS 预览、状态输出
- 直连 LiveKit 房间，不经过浏览器

### run_pion_gst_direct_capture.py — 单流 worker
- 负责一场比赛的接流、数据关联、收尾
- 使用 `SharedBettingDataReader` 从 dispatcher 共享文件读取数据（不独立请求数据站）
- 结束时用 `match_data_to_stream()` 筛选出本场比赛的数据
- 输出 `raw_betting_data.jsonl`（全量备份）和 `__betting_data.jsonl`（筛选后）
- 自动生成 `__timeline.csv`

### pion_gst_dispatcher.py — 多流调度
- 从 App bridge 获取 live 列表
- 从 proxy `/credentials` 获取 session（不自行调用 auto_login）
- 运行唯一的 `BettingDataPoller`，每 5s 请求一次数据站
- 全量数据写入 `shared_betting_data.jsonl`
- formal 模式下做比赛与盘口数据绑定
- 为命中的比赛分发 worker

### pion_gst_supervisor.py — 保活与控制
- `start`: 启动 dispatcher
- `stop`: 停止 dispatcher + 所有 worker（含 pgrep 兜底清理孤儿进程）
- `status`: 查看状态
- `list-artifacts`: 列出产物

## 主流程

1. App 内嵌页保持 `schedules/live` 登录态
2. dispatcher 从 App bridge 获取 live 列表
3. dispatcher 从 proxy `/credentials` 获取数据站 session
4. formal 模式下做比赛与盘口数据绑定
5. dispatcher 启动唯一的 BettingDataPoller（5s 轮询）
6. 为命中的比赛分发 worker
7. worker 用 `serverHost/token` 直连房间
8. worker 通过 SharedBettingDataReader 读取共享数据
9. 持续写：HLS 预览 + 归档段
10. 结束后生成 `__full.mp4` + 筛选 `__betting_data.jsonl` + `__timeline.csv`

## 数据采集架构 (2026-03-31 改版)

### 问题

之前每个 worker 独立运行 BettingDataPoller：
- 7 个 worker = 7 倍请求量
- 数据站检测频率异常会封号
- 每次启动 worker 还可能触发 auto_login 导致 doubleLogin

### 解决方案

```
Dispatcher:
  BettingDataPoller (唯一) ──5s──> 数据站 API
       │
       ▼ 写入
  shared_betting_data.jsonl (全量，持续追加)
       │
       ├── Worker A: SharedBettingDataReader (2s 读一次)
       │     └── match_data_to_stream() 筛选 → __betting_data.jsonl (只有比赛 A)
       │
       ├── Worker B: SharedBettingDataReader
       │     └── match_data_to_stream() 筛选 → __betting_data.jsonl (只有比赛 B)
       └── ...
```

- 数据站请求: 1 次/5s（不管多少个 worker）
- Worker 读文件: 无网络请求，无封号风险
- 每个 worker 的 `__betting_data.jsonl` 只包含自己那场比赛的数据

## Session 管理

### 当前流程

1. `data_site_proxy.py` 启动时 pre-login，缓存 session
2. dispatcher 从 `GET /credentials` 获取 session
3. 如果 session 失效（0 行数据），请求 `GET /credentials/refresh`
4. 只有 proxy 会调用 `auto_login()`，不会 doubleLogin

### bootstrap_credentials() 优先级

1. proxy `/credentials` — 最优先
2. 共享凭证文件 — 离线备选
3. dashboard — CDP 浏览器
4. `auto_login()` — 最后手段（会踢旧 session）

## 孤儿进程清理

### 问题

dispatcher 被 kill 后，worker 子进程（`start_new_session=True`）继续运行。`dispatcher_state.json` 已清空，无法追踪。

### 解决方案

`list_alive_worker_pids()` 两步发现：
1. 从 `dispatcher_state.json` 读已知 PID
2. 用 `pgrep -f` 搜索 `run_pion_gst_direct_capture.py` 和 `pion_livekit_gst_recorder` 进程

stop 时两步都会被 kill。

## 常用命令

```bash
# 启动
python3 pion_gst_supervisor.py start

# 查看状态
python3 pion_gst_supervisor.py status

# 停止 (会清理所有 worker 含孤儿进程)
python3 pion_gst_supervisor.py stop

# 列出产物
python3 pion_gst_supervisor.py list-artifacts
```

## 输出结构

每个 session:
- `recording.log` — 录制日志
- `session_result.json` — 最终结果
- `worker_status.json` — worker 状态
- `raw_betting_data.jsonl` — 全量原始数据（备份）

单场目录内:
- `...__betting_data.jsonl` — 筛选后的本场数据
- `...__timeline.csv` — 时间线
- `...__full.mp4` — 最终合并视频
- `hls/playlist.m3u8` — HLS 预览

## 当前边界

- 只覆盖 LiveKit 源
- 个别源流仍可能出现低帧输入
- 数据轮询频率 5s（用户设定，不可更快）
