# Rule Teaching 标注手册

> 更新时间：2026-03-27  
> 适用范围：`06_training_pool/03_rule_teaching`

## 1. 目标

这一层的任务不是“猜交易”，而是把短片段里的视觉事实映射成足球规则问题。

当前只回答三类问题：

- 球权 / 重启类型
- 犯规 / 纪律后果候选
- 时间流失候选

## 2. 先写什么

每条 rule teaching 先写这三样：

1. `visual_fact_summary`
2. `rule_rationale_short`
3. 规则字段

规则字段当前重点是：

- `last_touch_side`
- `exit_boundary`
- `restart_type`
- `discipline_outcome`
- `time_loss_candidate`

## 3. 写法原则

### `visual_fact_summary`

只写画面事实，不写下注或盘口语言。

推荐写法：

- `球从底线附近出界，但最后触球方暂不确定`
- `防守球员与进攻球员发生明显重接触，进攻方倒地`
- `比赛暂停，疑似需要短时间治疗`

### `rule_rationale_short`

只解释为什么要这样映射规则，不写交易推断。

推荐写法：

- `若最后触球方是防守方，则更接近角球候选`
- `接触动作较重，但仅凭这段片段还不能确认红牌`
- `治疗会占用有效比赛时间，应标记为时间流失候选`

## 4. 当前保守原则

- 看不清最后触球方时，`restart_type` 保持 `unknown`
- 看不清纪律尺度时，`discipline_outcome` 保持 `unknown`
- 不要为了填满字段而强行给结论

## 5. 当前不做

- 不在这一层输出赔率建议
- 不在这一层输出买入方向
- 不在这一层输出对冲金额
