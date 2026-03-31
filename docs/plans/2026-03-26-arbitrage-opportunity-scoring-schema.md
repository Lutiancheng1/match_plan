# 足球实时提醒 / 套利机会 Schema 说明

> 更新时间：2026-03-26  
> 适用范围：Phase 3 实时提醒层、对冲计算、复盘

## 1. 目标

这一阶段的输出不是“比赛描述”，而是：

- 当前提醒状态
- 是否值得出手
- 第一腿买哪边
- 第二腿何时做
- 是否具备对冲空间

## 2. 最小输出字段

- `opportunity_grade`
- `should_enter_first_leg`
- `first_leg_side`
- `first_leg_confidence`
- `alert_type`
- `trigger_family`
- `suggested_side`
- `entry_window_open`
- `hedge_watch_open`
- `pre_event_price`
- `target_hedge_price`
- `first_leg_stake`
- `recommended_hedge_stake`
- `estimated_locked_profit_low`
- `estimated_locked_profit_high`
- `voice_text`
- `invalid_reason`
- `expected_repricing_direction`
- `expected_repricing_strength`
- `expected_hedge_window_sec`
- `expected_arb_feasibility`
- `max_risk_flag`
- `rationale_short`

## 3. 新增字段约定

- `alert_type`
  - `watch`
  - `enter_first_leg`
  - `hedge_ready`
  - `invalidated`
- `trigger_family`
  - `strong_event`
  - `restart_corner`
- `voice_text`
  - 只允许 1-2 句关键提醒，不做长解说

## 4. 当前阶段的使用原则

- 评分器先服务于评估和复盘，不直接驱动自动下单
- 第一版允许输出建议方向和对冲计算，但不自动执行
- 若 `expected_arb_feasibility = none`，直接视为无价值样本

## 5. 和前后阶段的关系

- 输入来自：
  - 强事件识别结果
  - 盘口联合评测结果
- 输出服务于：
  - 人工复盘
  - 实时提醒
  - 对冲计算
  - 后续训练标签
