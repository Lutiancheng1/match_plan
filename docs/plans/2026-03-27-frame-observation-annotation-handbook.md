# 单帧 Observation 标注手册

> 更新时间：2026-03-27  
> 适用范围：`06_training_pool/01_frame_observation`

## 1. 目标

单帧标注的目标不是让模型直接判断交易，而是先让它学会回答最基础的足球画面问题：

- 这是不是 live 画面
- 比分牌能不能看清
- 比赛时间能不能看清
- 当前画面是否存在明显事件候选

## 2. 必标字段

每一帧都必须标：

- `scene_type`
- `score_detected`
- `match_clock_detected`
- `scoreboard_visibility`
- `replay_risk`
- `tradeability`
- `event_candidates`
- `confidence`
- `explanation_short`

## 3. 字段口径

### `scene_type`

候选值：

- `live_play`
- `replay`
- `scoreboard_focus`
- `crowd_or_bench`
- `stoppage`
- `unknown`

原则：

- 只要明显是回放包装、慢镜头、回放角标，优先标 `replay`
- 记分牌特写但不在正常比赛推进里，标 `scoreboard_focus`
- 替补席、教练席、观众镜头，标 `crowd_or_bench`

### `scoreboard_visibility`

候选值：

- `clear`
- `partial`
- `hidden`
- `unknown`

原则：

- 比分和时间都清楚，标 `clear`
- 只能看出其中一部分，标 `partial`
- 完全看不到比分牌，标 `hidden`

### `replay_risk`

候选值：

- `low`
- `medium`
- `high`

原则：

- live 画面正常推进时一般是 `low`
- 像回放前后切换、导播过渡镜头，标 `medium`
- 明显 replay 直接标 `high`

### `tradeability`

候选值：

- `tradeable`
- `watch_only`
- `ignore`

原则：

- 单帧阶段要保守
- 只有明显是 live 且信息完整时才可能给 `tradeable`
- replay、观众、广告、模糊镜头优先 `ignore`

### `event_candidates`

当前优先使用：

- `ball_out_of_play`
- `corner_candidate`
- `heavy_contact_foul`
- `injury_or_stoppage`
- `dangerous_attack`
- `replay_sequence`
- `none`

原则：

- 单帧只标“候选”，不标最终结论
- 看不清就标 `none`
- 不要把普通身体接触升级成 `heavy_contact_foul`

## 4. 标注优先级

优先保证这 4 类样本标准稳定：

- live vs replay
- 比分牌清晰度
- 出界候选
- 重接触 / 伤停候选

## 5. 实际标注顺序

每张图都按同一个顺序判断，避免口径漂移：

1. 先判 `scene_type`
2. 再判比分牌是不是清楚
3. 再判这是不是 replay 风险画面
4. 最后才标 `event_candidates`

建议原则：

- 如果 `scene_type=replay`，优先把 `replay_risk` 标成 `high`
- 如果比分牌完全看不到，`scoreboard_visibility` 优先标 `hidden`
- 如果看不清球或关键接触，不要硬标 `heavy_contact_foul`

## 6. `confidence` 口径

不是在标模型信心，而是在标“这张图对人工来说有多确定”。

建议区间：

- `0.0-0.3`
  画面模糊、导播切换中、信息不足
- `0.4-0.6`
  大致能判断，但仍需要上下文
- `0.7-0.9`
  画面事实比较清楚
- `1.0`
  极少使用，只给非常清晰、歧义很低的样本

## 7. 正反例提示

这些情况不要误标成高价值事件：

- 普通身体接触，不要直接标 `heavy_contact_foul`
- 普通界外球前摇，不要直接标 `corner_candidate`
- 教练席、观众席、庆祝镜头，不要标 `tradeable`

这些情况优先保留为候选而不是结论：

- 球接近边线或底线，但最后触球方不清楚
- 球员倒地，但无法确认是严重受伤还是普通犯规
- 禁区附近混战，但无法确认是否已经出界

## 8. `explanation_short` 写法

只写一句短事实，不写交易判断。

推荐写法：

- `live 画面，比分牌清楚，疑似出界候选`
- `慢镜头回放，不能作为实时事件判断`
- `球员倒地但接触细节不清，需要短片段复核`

## 9. 当前不做

- 不在单帧层判断角球最终成立
- 不在单帧层判断红牌是否最终成立
- 不在单帧层引入盘口或交易标签
