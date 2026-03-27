# 2026-03-26 Round 3 Formal Scoreboard Benchmark Summary

## Purpose

第三轮 benchmark 目标是验证“只看顶部记分牌/比赛时间区域”的 OCR 辅助路线，比较以下三个模型：

- `Qwen2.5-VL-7B-Instruct-4bit`
- `Qwen3.5-VL-35B-A3B-4bit-MLX-CRACK`
- `InternVL3-38B-4bit`

输入方式：

- 从每个 Gold clip 抽 3 张关键帧
- 只裁顶部区域
- 拼成 1 张 scoreboard contact sheet
- 让模型只输出：
  - `score_detected`
  - `match_clock_detected`
  - `confidence`

## Important Dataset Finding

在正式跑三模型之前，先用本机 `tesseract` 对 24 个 Gold clips 做了顶部裁剪可见性扫描。

尝试过的裁剪参数包括：

- `full_top18`
- `full_top22`
- `left_top18`
- `left_top24`
- `center_top20`

结果：

- `full_top18`: `0/24`
- `full_top22`: `0/24`
- `left_top18`: `1/24`，但文本明显是噪声误判
- `left_top24`: `1/24`，但文本明显是噪声误判
- `center_top20`: `0/24`

结论：

- 当前 Gold clips 的顶部区域里，**几乎没有稳定可见的比分牌/比赛时间条**
- 这不是模型 OCR 单独的问题，而是**样本本身不满足“可做比分牌 OCR benchmark”的前提**

## Formal Results

### Qwen2.5-VL-7B-Instruct-4bit

- run id: `Qwen2.5-VL-7B-Instruct-4bit__scoreboard_round3__1774526342`
- clip count: `24`
- visible scoreboard count: `0`
- json valid count: `18`
- non-empty score predictions: `0`
- non-empty clock predictions: `0`
- avg latency: `21639.64 ms`

### Qwen3.5-VL-35B-A3B-4bit-MLX-CRACK

- run id: `Qwen3.5-VL-35B-A3B-4bit-MLX-CRACK__scoreboard_round3__1774526940`
- clip count: `24`
- visible scoreboard count: `0`
- json valid count: `24`
- non-empty score predictions: `0`
- non-empty clock predictions: `0`
- avg latency: `1279.43 ms`

### InternVL3-38B-4bit

- run id: `InternVL3-38B-4bit__scoreboard_round3__1774527229`
- clip count: `24`
- visible scoreboard count: `0`
- json valid count: `20`
- non-empty score predictions: `0`
- non-empty clock predictions: `0`
- avg latency: `16174.32 ms`

## Interpretation

这轮结果不能解读成：

- “这三个模型 OCR 都不行”

更准确的解读是：

- 当前这 24 个 Gold clips **不适合作为比分牌 OCR benchmark 输入**
- 因为顶部裁剪区域里，几乎没有稳定可见的比分牌/时间条
- 在这种前提下，模型选择保守输出空字符串，反而是合理行为

## Current Recommendation

第三轮后续不要直接进入训练，先做两件事：

1. 新增一个“**scoreboard-visible clip**”筛选步骤
   - 只有确认画面内真的有比分牌/比赛时间条的 clip，才进入 OCR benchmark 池

2. 把当前 Gold clip 切片逻辑补强
   - 不只围绕盘口变化切片
   - 还要专门切一批“记分牌可见”的 clip

## Best Current Model Roles Remain

- 在线主力：`Qwen2.5-VL-7B-Instruct-4bit`
- 离线复核主力：`Qwen3.5-VL-35B-A3B-4bit-MLX-CRACK`

这轮第三轮 benchmark 没有推翻这个判断，因为问题主要出在样本输入，不是角色分工。
