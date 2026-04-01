# 2026-04-01 599 文字直播对齐接入说明

## 目标

在“本地录视频 + 本地采盘口数据”的主链里，增加一条尽量轻量的辅助对齐链：

- 不改原来的录制主链
- 不把系统复杂化成多数据源融合平台
- 先解决一个最核心的问题：
  我们怎么知道“视频”和“盘口数据”大致对齐了

当前选用的方案是：
- 接 599 文字直播
- 用 599 的比赛事件时间作为视频时间轴辅助锚点
- 再用本地 `betting_data` 做比分变化交叉校验

## 当前接入位置

核心代码：
- `recordings/pion_gst_direct_chain/live_text_599.py`
- `recordings/pion_gst_direct_chain/run_pion_gst_direct_capture.py`

它是单场 worker 内的一条旁路：

1. worker 启动
2. 启动 `LiveTextPoller599`
3. 通过队名 / 联赛 / 开赛时间找到 599 `thirdId`
4. 回溯历史文字直播
5. 增量轮询最新 30 条 `matchLive`
6. 用 `AlignmentEngine` 计算：
   - kickoff 推算
   - `match_time -> video_pos`
   - 比分漂移校验
7. 实时把结果写入单场目录

## 当前存储设计

### 会实时落盘

每个 session 根目录：
- `recording.log`
- `worker_status.json`
- `session_result.json`
- `raw_betting_data.jsonl`

每个单场目录：
- `__betting_data.jsonl`
- `__live_events.jsonl`
- `__full.mp4`
- `__timeline.csv`

### 599 的存储边界

599 事件当前只做 session / 单场级存储：
- 会写进 `__live_events.jsonl`
- 会写摘要到 `worker_status.json.liveText599`
- 会写摘要到 `session_result.json.live_text_599`

当前**不会**：
- 写进全局 `history.db`
- 参与生成全局历史表
- 直接驱动 `timeline.csv`

原因：
- 先把对齐链跑通
- 先保留清晰边界
- 暂时不把系统复杂化

## 当前实时日志节点

这些节点都会实时写进 `recording.log`：

- `599 匹配成功`
- `599 匹配失败`
- `599 kickoff推算`
- `599 kickoff校验`
- `599 历史回溯`
- `599 轮询#N`
- `599 比分校验`
- `599 文字直播落盘(periodic)`
- `599 文字直播最终写入`

App 日志页会自动显示这些内容，因为它直接 tail 当前 worker 的 `recording.log`。

## 当前方案的实现逻辑

### 1. 比赛匹配

用这些条件给 599 候选打分：
- 主队名
- 客队名
- alias 命中
- 联赛命中
- 开赛时间距离
- 年龄段一致性
- 女足标记一致性

阈值：
- `MATCH_CONFIDENCE_THRESHOLD = 140`

### 2. kickoff 推算

用最新文字事件的 `match_time_ms` 反推：

`kickoff_utc = observed_at - latest_match_time_ms`

后续轮询只在漂移不大时做渐进修正。

### 3. 视频映射

有了 `kickoff_utc` 和 `video_start_utc` 后：

`video_pos_sec = kickoff_video_offset + match_time_ms / 1000`

### 4. 本地比分交叉校验

录制主链本来就在写 `__betting_data.jsonl`。

因此当 599 出现进球事件时，可以：
- 从文字里提取比分
- 去本地 `betting_data` 找第一次出现同比分的时间
- 计算 `drift_sec`

这一步只是辅助校验，不是硬阻断条件。

## 2026-04-01 实测结果

### 已完成的真实录制验证

失败链路验证：
- `session_pgst_599_smoketest_20260401_002`
- 比赛：`ESM Kolea vs Bechar Djedid`
- 结果：录制成功，599 线程启动成功，失败日志成功写出
- 原因：当前 599 无可信对应比赛，无法 resolve

成功链路验证：
- `session_pgst_599_smoketest_20260401_003`
- 比赛：`Vålerenga W vs Røa W`
- 结果：
  - resolve 成功
  - backfill 成功
  - poll 成功
  - periodic flush 成功
  - final write 成功
  - `__live_events.jsonl = 752 rows`

### 这次发现并修掉的 bug

问题：
- 女足 `W` 标记没有被 599 匹配逻辑正确识别
- 导致 `Vålerenga W vs Røa W` 被误判 `gender_mismatch`
- best score 卡在 `120 < 140`

修复：
- 改为复用主链已有的 `has_women_marker()`

修复后：
- 同一场变为 `home_match + away_match + time<=0m`
- `score = 240`
- 能稳定 resolve 成功

## 当前结论

这条方案现在已经可以先留着用：

- 复杂度可控
- 边界清楚
- 有 session 级痕迹
- 有单场级原始事件文件
- 有实时日志
- 已经跑过一次真实成功链路

## 暂不做

今天先不做这些，以免系统过重：

- 不接 599 赔率 / 走势做多源融合
- 不接 599 视频流做视频到视频对齐
- 不把 599 事件写入 `history.db`
- 不改 `timeline.csv` 生成逻辑

后面如果需要增强，再往上叠：
- `599 odds / line`
- 视频 OCR 记分牌校验
- 全局历史库归档
