# 强事件标签体系

> 更新时间：2026-03-26  
> 适用范围：`analysis_vlm` 第一阶段、clip 样本标注、强事件 benchmark

## 1. 目的

这份文档定义第一阶段真正要识别的内容：

- 不是“看懂整场比赛”
- 而是识别那些**可能触发盘口重定价**的场内强事件

## 2. 一级标签

第一阶段统一采用以下一级标签：

- `injury_event`
- `red_card_risk`
- `medical_stoppage`
- `time_decay_pressure`
- `momentum_pressure`
- `none`

## 3. 一级标签定义

### 3.1 `injury_event`

定义：

- 球员倒地
- 身体接触剧烈
- 明显痛苦动作
- 疑似不能立即继续比赛

关注点：

- 受伤严重程度
- 是否为核心球员
- 是否可能被换下

### 3.2 `red_card_risk`

定义：

- 恶意或高强度犯规
- 铲球/踩踏/肘击等高风险动作
- 可能产生黄牌升级或红牌

关注点：

- 判罚是否即将发生
- 少一人是否会改变盘口预期

### 3.3 `medical_stoppage`

定义：

- 医疗队进场
- 比赛明显暂停
- 球员接受治疗

关注点：

- 停顿时间长度
- 是否构成“安全时间流失”

### 3.4 `time_decay_pressure`

定义：

- 比赛进入后段
- 当前比分/盘口下，剩余时间价值快速变化
- 任何拖延、停顿、出界、治疗都会显著影响盘口

关注点：

- 事件发生时比赛所处时间段
- 剩余时间能否支撑原盘口兑现

### 3.5 `momentum_pressure`

定义：

- 明显持续压制
- 强攻 / 被围攻 / 长时间出不去球
- 容易引发下一次盘口变化的局势积累

关注点：

- 是否是短时偶然镜头
- 是否已有连续性

### 3.6 `none`

定义：

- 当前 clip 没有明显强事件
- 或不足以支持任何一个一级标签

## 4. 每个样本需要的核心字段

第一阶段每条 clip 至少标这些字段：

- `primary_event_label`
- `secondary_event_labels`
- `severity`
- `affected_side`
- `expected_pricing_impact_direction`
- `expected_pricing_impact_confidence`
- `stoppage_seconds_estimate`
- `manual_review_status`

## 5. 推荐标注尺度

### `severity`

- `low`
- `medium`
- `high`
- `critical`

### `affected_side`

- `home`
- `away`
- `both`
- `unknown`

### `expected_pricing_impact_direction`

- `home_bullish`
- `away_bullish`
- `home_bearish`
- `away_bearish`
- `unclear`

## 6. 当前阶段的标注原则

- 优先保守，不要过度脑补
- 只标“从画面中能较明确支持”的事件
- 争议样本可暂存 `manual_review_status = pending`
- 如果只是普通犯规，不要自动升级为 `red_card_risk`
- 如果只是普通倒地，不要自动升级为 `injury_event`

## 7. 和后续阶段的关系

这套标签体系主要服务：

- Phase 1：强事件识别模型
- Phase 2：事件 + 盘口联合评测

它还不是最终套利决策标签，但它会成为后续“盘口影响判断”的输入基础。
