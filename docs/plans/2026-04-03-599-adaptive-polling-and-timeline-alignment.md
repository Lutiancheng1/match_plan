# 599 自适应轮询 & 时间线对齐技术文档

> 日期：2026-04-03
> 目的：记录 599 轮询限速策略、三条时间线对齐原理，供后续开发和运维参考

---

## 一、599 轮询架构

### 1.1 两种模式

| 模式 | 类 | 场景 | 说明 |
|------|---|------|------|
| **独立轮询** | `LiveTextPoller599` | 单场录制/调试 | 每个 worker 自己轮询 599 API |
| **集中轮询** | `Shared599Writer` + `Shared599Reader` | dispatcher 多场并发 | dispatcher 统一轮询，worker 读共享文件 |

代码位置：`recordings/pion_gst_direct_chain/live_text_599.py`

### 1.2 自适应限速（2026-04-03 改动）

**问题**：原先固定 5s 轮询间隔，角球事件延迟 10-20s，加上轮询间隔最坏情况延迟 25-35s。希望缩短到 2s，但多场并发时不能打爆 599 API。

**方案**：`Shared599Writer` 的 `poll_interval` 改为动态属性：

```python
poll_interval = max(base_interval, N × MIN_REQUEST_GAP)
```

**参数**：

| 参数 | 值 | 含义 |
|------|---|------|
| `base_interval` | 2.0s | 少量比赛时的最快轮询间隔 |
| `MIN_REQUEST_GAP` | 0.5s | 两个 HTTP 请求之间的最小间距 |

**效果**：

| 并发比赛数 | 实际周期 | 每场轮询频率 | 总请求速率 |
|-----------|---------|------------|-----------|
| 1-4 场 | **2s** | 每 2s 一次 | ≤ 2 req/s |
| 5 场 | 2.5s | 每 2.5s 一次 | 2 req/s |
| 10 场 | **5s** | 每 5s 一次 | 2 req/s |
| 20 场 | **10s** | 每 10s 一次 | 2 req/s |

**核心保证**：无论多少场比赛，总请求速率 ≤ `1/MIN_REQUEST_GAP = 2 req/s`。

**Stagger**：在每个周期内，N 场比赛均匀分布在 `poll_interval` 时间内依次发送请求，不会瞬间并发。

### 1.3 使用方式

```bash
# 单场录制，2s 轮询
python3 recordings/pion_gst_direct_chain/run_pion_gst_direct_capture.py \
    --live-text-599-poll-seconds 2 ...

# dispatcher 集中轮询（自动根据比赛数量调整）
# 在 pion_gst_dispatcher.py 中 Shared599Writer 默认 base=2s
```

### 1.4 风控注意事项

- 599 API 是 HTTPS 请求（`fb-i.599.com`），每次轮询 1 个请求
- 当前没有发现明确的限流策略，但建议总速率不超过 2 req/s
- 如果出现 429/限流，可调大 `MIN_REQUEST_GAP`（如 1.0s → 总速率 1 req/s）
- `LiveTextPoller599` 的下限也从 5s 降到了 2s

---

## 二、三条时间线对齐

### 2.1 时间基准

录制系统中有三个独立时钟：

| 时间源 | 来源 | 用途 |
|--------|-----|------|
| **视频 video_pos_sec** | 视频文件的播放位置（秒） | **主基准轴** |
| **599 match_time** | 599 API 的 `time` 字段（ms） | 事件标注 |
| **RETIMESET** | 数据站轮询的比赛时钟字段 | 赔率行标注 |

### 2.2 对齐关系

```
video_pos_sec = offset + match_time_sec
```

- `offset` 由 `AlignmentEngine` 在录制时校准（599 kickoff 事件推断）
- **599 match_time 和 RETIMESET 共用同一时基**，互差 2-8 秒
- 同一个 `offset` 可用于两者的 vpos 计算

### 2.3 广播时钟（视频 OSD）

视频画面上叠加的比赛时钟（如 "19:41"）与 599/RETIMESET 时钟**不是同一时基**：

```
广播时钟 = 599 时钟 + 60~90秒（赛前仪式/入场时间，因赛事而异）
```

**重要**：
- 广播时钟领先 599/RETIMESET 约 60-90 秒
- 如果用 OCR 读取广播时钟做校准，得到的 offset 不能与 599 offset 混用
- 应分别标注 `offset_source = "599_kickoff"` 或 `"ocr_broadcast"`

### 2.4 数据标注字段

每行 betting_data.jsonl 录制时自动标注：

```json
{
  "_video_pos_sec": 1185.55,   // 视频基准时间轴
  "_match_time_sec": 1201.0,   // 从 RETIMESET 解析
  "_match_time_ms": 1201000,
  "_match_half": 1,
  "_match_clock": "20:01"
}
```

每条 599 live_events.jsonl 录制时自动标注：

```json
{
  "_video_pos_sec": 1109.54,   // 视频基准时间轴
  "time": 1124989,             // 599 原始 match_time (ms)
  "code": 1025                 // 事件代码
}
```

### 2.5 对齐精度

| 对齐对象 | 精度 | 说明 |
|---------|------|------|
| 599 事件 → video_pos | ±2-5s | AlignmentEngine kickoff 校准 |
| RETIMESET → video_pos | ±5-10s | 复用 599 offset + 两时钟 2-8s 差 |
| 广播时钟 → video_pos | ±1s | OCR 直接校准（如果有的话） |

---

## 三、599 事件延迟（角球专项）

599 角球事件（code=1025 HOME / 2049 AWAY）从球场发生到 API 报告有延迟：

| 延迟 | 范围 | 中位数 |
|------|------|--------|
| 角球准备 → 599报告 | 0-20s | **~11s** |

详细验证见：`docs/plans/2026-04-03-corner-kick-alignment-verification.md`

**训练数据采样窗口**：
- 正确：`[vpos-20s, vpos-2s]`
- 错误：`[-2, 0, +2]`（会采到进球庆祝/回放）

---

## 四、关键文件清单

| 文件 | 用途 |
|------|------|
| `recordings/pion_gst_direct_chain/live_text_599.py` | AlignmentEngine, LiveTextPoller599, Shared599Writer/Reader |
| `recordings/pion_gst_direct_chain/api_599_client.py` | 599 API 客户端（get_match_list, get_match_info） |
| `recordings/pion_gst_direct_chain/run_pion_gst_direct_capture.py` | Worker：录制+标注 |
| `recordings/pion_gst_direct_chain/pion_gst_dispatcher.py` | Dispatcher：集中轮询+worker管理 |
| `recordings/backfill_video_alignment.py` | 历史录制回补对齐 |
| `recordings/backfill_timeline_csv.py` | 生成 timeline.csv |
| `analysis_vlm/tools/extract_corner_verification_frames.py` | 角球帧提取验证工具 |
