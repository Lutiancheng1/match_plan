# 足球“懂球第一阶段”落地方案

> 更新时间：2026-03-27  
> 当前阶段：Phase 1 起步  
> 目标：先让模型看懂足球画面，再逐步进入规则、盘口、交易

## 1. 当前唯一主线

当前不做提醒层，不做对冲层，不做语音层优先开发。

当前唯一主线是：

1. 建立足球知识库和任务定义
2. 统一 observation schema
3. 建立单帧与短片段教学样本
4. 先训练 / 验证“懂球”能力

## 2. 目录与数据流

### 原始来源

- 原始录制：`/Volumes/990 PRO PCIe 4T/match_plan_recordings`
- Gold 比赛清单：`/Volumes/990 PRO PCIe 4T/match_plan_dataset_library/01_gold_matches/current_gold_matches.json`

### Phase 1 训练池

- `06_training_pool/00_source_registry`
- `06_training_pool/01_frame_observation`
- `06_training_pool/02_clip_observation`
- `06_training_pool/03_rule_teaching`
- `06_training_pool/04_holdout_eval`
- `06_training_pool/05_reviews`

## 3. 一步一步推进

### Step 1. 建训练池和来源索引

目标：

- 固定 Phase 1 的样本目录结构
- 不再把教学样本混在 benchmark 或黄金 clip 目录里

完成标准：

- 训练池目录建立完成
- 所有新增教学样本都进入 `06_training_pool`

### Step 2. 先做单帧 observation 样本

目标：

- 从现有足球 Gold 素材中均匀抽帧
- 先教模型识别静态事实

每条单帧样本至少包含：

- `scene_type`
- `score_detected`
- `match_clock_detected`
- `scoreboard_visibility`
- `replay_risk`
- `tradeability`
- `event_candidates`
- `confidence`
- `explanation_short`

完成标准：

- 第一批每场 `60-100` 帧候选
- 总量先做到 `800-1200` 帧
- 单帧标注以 `2026-03-27-frame-observation-annotation-handbook.md` 为准

### Step 3. 再做短片段 observation 样本

目标：

- 补动态事实样本
- 教模型理解接触、倒地、治疗、出界、底线、角球候选
- 当前切片策略不是直接判定事件，而是优先产出“候选课件”

推荐 clip 时长：

- 出界 / 角球：`2-4s`
- 重踩 / 犯规：`2-4s`
- 倒地 / 医疗暂停：`3-6s`

完成标准：

- 第一批做到 `300-500` 个 micro-clips
- 每条 clip 必须带 `course_target`
- 每条 clip 必须带 `review_priority`
- 高优先级 clip 优先覆盖：
  - `contact_restart_scan`
  - `discipline_event_review`
  - `goal_or_restart_review`
- 当前 clip builder 只能自动产“候选课件”，不直接断定重踩、角球、红牌

### Step 4. 规则教学样本

目标：

- 把视觉事实映射到足球规则
- 当前 rule teaching 先不追求最终裁判结论，而是要求人工先回答固定规则问题

当前先教：

- 最后触球方
- 边线 / 底线
- 角球 / 球门球
- 重踩 / 红牌风险候选
- 治疗 / 时间流失候选

当前 rule teaching 记录至少要带：

- `teaching_task`
- `target_rule_question`
- `expected_fact_focus`
- `expected_rule_focus`
- `last_touch_side`
- `exit_boundary`
- `restart_type`
- `discipline_outcome`
- `time_loss_candidate`
- `rule_rationale_short`

执行入口：

- `analysis_vlm/tools/build_rule_teaching_samples.py`

### Step 5. 再进入盘口层

只有当 observation 和规则层稳定后，才重新接盘口数据。

## 4. 当前执行入口

### 已完成

- 训练池标准目录已建立
- 单帧候选样本已落第一版
- 短片段候选样本已落第一版
- `03_rule_teaching` stub 已建立

### 当前首个执行脚本

- `analysis_vlm/tools/build_frame_observation_samples.py`

用途：

- 从 Gold 足球素材生成单帧 observation 候选样本
- 输出图片、记录和 manifest
- 服务“先看懂静态足球画面”

### 当前第二个执行脚本

- `analysis_vlm/tools/build_clip_observation_samples.py`

用途：

- 从 Gold 足球素材生成短片段 observation 候选样本
- 输出 clip、记录和 manifest
- 当前会额外生成 `dense_review` 课程片段，优先供重接触 / 出界 / 角球候选人工复核
- 标注口径以 `2026-03-27-clip-observation-annotation-handbook.md` 为准

### 当前第三个执行脚本

- `analysis_vlm/tools/build_rule_teaching_samples.py`

用途：

- 从 clip observation 记录生成规则教学 stub
- 给后续“事实 -> 规则”标注层打底
- 当前会根据 `course_target` 自动生成第一版规则问题模板
- 标注口径以 `2026-03-27-rule-teaching-annotation-handbook.md` 为准

### 当前第四个执行脚本

- `analysis_vlm/tools/build_phase1_review_queue.py`

用途：

- 汇总主训练池和单场测试池的高优先级 clip
- 统一输出人工复核顺序

### 当前第五个执行脚本

- `analysis_vlm/tools/build_phase1_review_assets.py`

用途：

- 给高优先级 clip 自动抽关键帧
- 生成 contact sheet 和 review packet
- 让人工复核不必先逐个手动打开视频

### 当前外部来源登记

- 官方来源清单：
  `/Users/niannianshunjing/match_plan/docs/plans/2026-03-27-official-football-video-sources.md`
- 训练池来源登记：
  `/Volumes/990 PRO PCIe 4T/match_plan_dataset_library/06_training_pool/00_source_registry/current_official_sources.json`

## 5. 当前不做

- 不做实时提醒 demo
- 不做对冲金额 demo
- 不做语音播报 demo
- 不把交易逻辑提前压到 observation 前面
