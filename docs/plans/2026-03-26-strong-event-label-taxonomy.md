# 足球强事件标签体系

> 更新时间：2026-03-26  
> 适用范围：`analysis_vlm` 主线 A、clip 样本标注、强事件 benchmark

## 1. 目的

这份文档定义足球主线 A 在第一阶段真正要识别的内容：

- 不是“看懂整场比赛”
- 而是识别那些**可能触发盘口重定价**的足球场内强事件

## 2. 一级标签

- `injury_event`
- `red_card_risk`
- `medical_stoppage`
- `time_decay_pressure`
- `momentum_pressure`
- `none`

## 3. 定义重点

### `injury_event`

- 球员倒地
- 身体接触剧烈
- 明显痛苦动作
- 疑似不能立即继续比赛

### `red_card_risk`

- 恶意或高强度犯规
- 铲球 / 踩踏 / 肘击等高风险动作
- 可能产生黄牌升级或红牌

### `medical_stoppage`

- 医疗队进场
- 比赛明显暂停
- 球员接受治疗

### `time_decay_pressure`

- 比赛进入后段
- 当前比分 / 盘口下，剩余时间价值快速变化
- 任何拖延、停顿、出界、治疗都会显著影响盘口

### `momentum_pressure`

- 明显持续压制
- 强攻 / 被围攻 / 长时间出不去球
- 容易引发下一次盘口变化的局势积累

## 4. 每个样本需要的核心字段

- `primary_event_label`
- `secondary_event_labels`
- `severity`
- `affected_side`
- `expected_pricing_impact_direction`
- `expected_pricing_impact_confidence`
- `stoppage_seconds_estimate`
- `entry_window_open`
- `entry_window_state`
- `voice_text`
- `trade_context_short`
- `manual_review_status`

## 5. 标注原则

- 优先保守，不要过度脑补
- 只标“从画面中能较明确支持”的事件
- 争议样本可暂存 `manual_review_status = pending`
- 如果只是普通犯规，不要自动升级为 `red_card_risk`
- 如果只是普通倒地，不要自动升级为 `injury_event`

## 6. 和后续阶段的关系

这套标签体系主要服务：

- Phase 1：强事件识别模型
- Phase 2：事件 + 盘口联合评测
- Phase 3：实时提醒与对冲计算
