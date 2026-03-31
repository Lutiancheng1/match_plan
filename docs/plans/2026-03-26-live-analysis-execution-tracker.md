# 足球实时套利助手执行跟踪文档

最后更新：2026-03-26 08:30 America/Los_Angeles

## 1. 项目真实目标

本项目的最终目标不是“让模型看懂比赛解说”，而是：

- 训练一个足球实时观察模型
- 让它实时跟看足球直播
- 先看懂足球画面
- 再看懂足球规则
- 再看懂盘口变化
- 最后才看懂交易逻辑
- 服务人工套利执行

一句话定义：

**先把足球 observation 做准，再在其上逐层接规则、盘口和交易逻辑。**

## 2. 总体阶段

### Phase 0. 合法素材生产

- 只录“视频可播 + 数据已绑定”的比赛
- 自动巡检、自动录制、自动关闭结束窗口
- 自动素材过滤，Gold/Silver/Reject 分层

当前状态：进行中，主链可用

### Phase 1. 足球知识库与任务定义

当前状态：本轮已明确新的主顺序，下一步转入知识库和任务定义收口

### Phase 2. observation schema 与 benchmark 统一

当前状态：单帧 / 多帧 JSON 开始向统一 observation contract 收口

### Phase 3. 教学样本建设

当前状态：素材量仍偏少，教学样本远远不够

### Phase 4. 足球实时观察

当前状态：还在“懂球”前置阶段，尚未达到可实时观察标准

### Phase 5. 事件 + 盘口联合评测

- 给模型看比赛片段
- 同时给它对应时间窗口的盘口数据
- 让模型判断：
  - 是否会引发重定价
  - 向哪边变
  - 是否值得先手买入

当前状态：联合评测输入已生成；runner 第一版已实现并完成首轮 smoke test

### Phase 6. 规则 / 提醒 / 蒸馏

当前状态：暂缓，不再提前推进 demo 层

当前状态：未开始

## 3. 当前已完成

### 录制与数据侧

- 录制主链稳定为只录“视频可播 + 数据已绑定”
- 当前生产链已经锁到足球 `FT`
- 每轮巡检重新比对当前全部直播
- 数据源健康检查已成为录制前硬门槛

### 素材侧

- 自动素材过滤脚本已完成
- Gold/Silver/Reject 长期素材库目录已建立
- Gold clip 自动切片已完成
- 原始 raw 目录与主 manifest 已清理为足球专用

### 分析与 benchmark

- 本地模型候选池已整理
- Round 1 benchmark 已完成
- Round 2 clip benchmark 已完成
- Round 3 scoreboard OCR benchmark 已完成
- Phase 2 联合评测 runner 已实现

### Phase 1 / Phase 2 数据结构

- 强事件标签 schema 已完成
- 重启 / 角球 observation schema 已完成
- 套利评分 schema 已完成
- 联合评测 schema 已完成
- 强事件标签 skeleton 已生成
- 事件 + 盘口联合评测输入已生成
- observation schema 收口中

## 4. 当前产物位置

- 总计划：
  [/Users/niannianshunjing/match_plan/docs/plans/2026-03-25-live-analysis-and-local-training-master-plan.md](/Users/niannianshunjing/match_plan/docs/plans/2026-03-25-live-analysis-and-local-training-master-plan.md)
- 强事件标签体系：
  [/Users/niannianshunjing/match_plan/docs/plans/2026-03-26-strong-event-label-taxonomy.md](/Users/niannianshunjing/match_plan/docs/plans/2026-03-26-strong-event-label-taxonomy.md)
- 角球 / 重启专项：
  [/Users/niannianshunjing/match_plan/docs/plans/2026-03-26-football-corner-restart-plan.md](/Users/niannianshunjing/match_plan/docs/plans/2026-03-26-football-corner-restart-plan.md)
- 联合评测计划：
  [/Users/niannianshunjing/match_plan/docs/plans/2026-03-26-event-odds-joint-evaluation-plan.md](/Users/niannianshunjing/match_plan/docs/plans/2026-03-26-event-odds-joint-evaluation-plan.md)
- 套利评分 schema 说明：
  [/Users/niannianshunjing/match_plan/docs/plans/2026-03-26-arbitrage-opportunity-scoring-schema.md](/Users/niannianshunjing/match_plan/docs/plans/2026-03-26-arbitrage-opportunity-scoring-schema.md)
- 足球知识库与任务定义：
  [/Users/niannianshunjing/match_plan/docs/plans/2026-03-27-football-knowledge-base-and-task-definition.md](/Users/niannianshunjing/match_plan/docs/plans/2026-03-27-football-knowledge-base-and-task-definition.md)
- observation 与教学样本计划：
  [/Users/niannianshunjing/match_plan/docs/plans/2026-03-27-football-observation-and-teaching-sample-plan.md](/Users/niannianshunjing/match_plan/docs/plans/2026-03-27-football-observation-and-teaching-sample-plan.md)

## 5. 当前推荐模型分工

- 在线主力候选：
  - `Qwen3.5-VL-9B-8bit-MLX-CRACK`
- 离线复核主力候选：
  - `Qwen3.5-VL-35B-A3B-4bit-MLX-CRACK`
- 教师模型候选：
  - `Qwen3.5-VL-122B-A10B-4bit`

## 6. 当前进行中的事项

### 进行中 1：Phase 2 联合评测 runner

目标：

- 让模型同时看：
  - clip 关键帧
  - 盘口时间窗口摘要

当前状态：第一版已实现，三模型第一轮横向对比已完成；prompt v2 已验证有效

### 进行中 2：知识库与 observation contract

目标：

- 统一单帧 / 多帧 / 联合 observation contract
- 明确模型先学什么、怎么学、怎么标

当前状态：

- 正在纠偏到“先懂球、后交易”的顺序

### 进行中 3：本地 oMLX 服务恢复与稳定化

目标：

- 保证联合评测、后续强事件 runner、模型对比都能持续调用本地 OMLX
- 避免因为本地服务中断把 benchmark 误判成模型失败

## 7. 下一步

### 下一步 1

抽样复核强事件联合评测输出，归纳三模型错误模式：

- 哪些是假阳性
- 哪些是方向判断错
- 哪些是第一腿方向错

### 下一步 2

开始补第一批高质量足球教学样本

### 下一步 3

建立角球 / 重启 observation 事实样本，不直接进入交易层

## 8. 当前阻塞与风险

- 当前 Gold 素材数量仍偏少
- 很多比赛虽然有直播，但没有同场盘口数据，不能进入训练 / 评测
- scoreboard OCR 受比分牌可见性影响大，暂时不应把它当主线能力
- 当前 observation 数据量和教学样本仍明显不足
- 角球线尚无成型 benchmark 和金标集
