# Pion + GStreamer 主录制链

这是当前正式采用的录制后端。

## 目标

- 不再依赖浏览器录屏
- 只保留 App 内嵌页提供登录态和 bootstrap
- 用原生 WebRTC 直连接流
- 输出可归档成片和运行中预览

## 当前组成

- `main.go`
  - Go 录制器
  - 负责接流、主视频归档、HLS 预览、状态输出
- `run_pion_gst_direct_capture.py`
  - 单流 worker
  - 负责一场比赛的接流、数据关联、收尾
- `pion_gst_dispatcher.py`
  - 多流调度
  - 负责发现比赛、匹配盘口数据、分发 worker
- `pion_gst_supervisor.py`
  - 保活与控制入口

## 当前主流程

1. App 内嵌页保持 `schedules/live` 登录态  
2. dispatcher 从 App bridge 获取 live 列表  
3. formal 模式下做比赛与盘口数据绑定  
4. 为命中的比赛分发 worker  
5. worker 用 `serverHost/token` 直连房间  
6. 持续写：
   - 单场盘口数据
   - HLS 预览
   - 归档段
7. 结束后生成 `__full.mp4`

## 数据链说明

### 不是每场单独打一条 API

当前是一条**共享数据轮询链**：

- 同一轮数据请求只打一次
- 然后本地再分发给各场 worker

### 当前频率

- 目标轮询：`1s`
- 单场 `__betting_data.jsonl` 当前通常接近 `1s ~ 3s`
- 如果上游接口本身变慢，落盘也会跟着变慢

### formal 行为

- 只录已经绑定到盘口数据的比赛
- 会持续写单场 `__betting_data.jsonl`
- 结束时会再做一次 flush

## 视频链说明

### 预览

- HLS 主要用于边录边看
- 不作为最终质量标准

### 归档

- 归档主线最终目标是稳定产出 `__full.mp4`
- 当前判断最终视频质量时，请优先看 `__full.mp4`

## 常用命令

### 启动 supervisor

```bash
python3 /Users/niannianshunjing/match_plan/recordings/pion_gst_direct_chain/pion_gst_supervisor.py start
```

### 查看状态

```bash
python3 /Users/niannianshunjing/match_plan/recordings/pion_gst_direct_chain/pion_gst_supervisor.py status
```

### 停止

```bash
python3 /Users/niannianshunjing/match_plan/recordings/pion_gst_direct_chain/pion_gst_supervisor.py stop
```

### 列出产物

```bash
python3 /Users/niannianshunjing/match_plan/recordings/pion_gst_direct_chain/pion_gst_supervisor.py list-artifacts
```

## 输出结构

每个 session 类似：

- `recording.log`
- `session_result.json`
- `worker_status.json`

单场目录内常见：

- `...__betting_data.jsonl`
- `hls/playlist.m3u8`
- `...__full.mp4`

## 当前已验证结论

- App 会话已可直接提供：
  - live 列表
  - `watch` bootstrap
- formal 模式已能筛出匹配到盘口数据的比赛并启动录制
- 最终 `full.mp4` 已经明显比旧版本稳定
- 修复前的大量测试和坏产物已清理

## 当前边界

- 只覆盖 `LiveKit`
- 个别源流仍可能出现低帧输入
- 单场数据频率当前还没有完全锁死到绝对 `1s`
