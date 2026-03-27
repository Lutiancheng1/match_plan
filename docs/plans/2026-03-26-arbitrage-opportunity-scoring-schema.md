# 套利机会评分 Schema 说明

> 更新时间：2026-03-26  
> 适用范围：Phase 3 套利机会评分器

## 1. 目标

这一阶段的输出不是“比赛描述”，而是：

- 是否值得出手
- 第一腿买哪边
- 第二腿何时做
- 是否具备对冲空间

## 2. 核心问题

套利机会评分器要回答：

1. 当前事件是否足以让盘口重定价？
2. 现在是不是先手窗口？
3. 先手应该买哪边？
4. 预期第二腿会在多久后出现？
5. 这一机会是否具备锁利空间？

## 3. 最小输出字段

- `opportunity_grade`
- `should_enter_first_leg`
- `first_leg_side`
- `first_leg_confidence`
- `expected_repricing_direction`
- `expected_repricing_strength`
- `expected_hedge_window_sec`
- `expected_arb_feasibility`
- `max_risk_flag`
- `rationale_short`

## 4. 字段解释

### `opportunity_grade`

候选值：

- `A`
- `B`
- `C`
- `D`
- `reject`

### `should_enter_first_leg`

表示：

- 当前是否值得先手下第一腿

### `first_leg_side`

候选值：

- `home`
- `away`
- `none`

### `expected_repricing_direction`

候选值：

- `home_price_down`
- `home_price_up`
- `away_price_down`
- `away_price_up`
- `unclear`

### `expected_arb_feasibility`

候选值：

- `high`
- `medium`
- `low`
- `none`

### `max_risk_flag`

候选值：

- `safe_enough`
- `uncertain_event`
- `repricing_already_happened`
- `liquidity_or_timing_risk`
- `do_not_trade`

## 5. 当前阶段的使用原则

- 评分器先服务于评估和复盘，不直接驱动自动下单
- 如果 `opportunity_grade` 不是 `A/B`，默认不进入人工执行建议
- 若 `expected_arb_feasibility = none`，直接视为无价值样本

## 6. 和前后阶段的关系

- 输入来自：
  - 强事件识别结果
  - 盘口联合评测结果
- 输出服务于：
  - 人工复盘
  - 机会排序
  - 后续训练标签

## 7. 当前结论

在没有稳定的：

- 强事件标签体系
- 事件 + 盘口联合评测

之前，不应直接训练或上线套利机会评分器。
