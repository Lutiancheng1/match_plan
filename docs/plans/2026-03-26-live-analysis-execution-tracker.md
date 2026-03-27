# 直播盘口套利系统执行跟踪文档

最后更新：2026-03-26 08:30 America/Los_Angeles
维护规则：每次项目发生代码、流程、数据标准、benchmark、模型池、录制规则、素材规则变更时，都必须同步更新本文件。

## 1. 项目真实目标
本项目的最终目标不是“让模型看懂比赛解说”，而是：

- 识别直播中的重定价事件
- 结合实时盘口与时间变化，提前判断赔率将如何变化
- 在盘口尚未完全反应前先手买入
- 在盘口变化后补第二腿，对冲形成低风险套利空间

一句话定义：

**通过识别直播中的重定价事件，提前判断盘口即将如何变化，并利用事件前后赔率差完成低风险对冲套利。**

## 2. 总体阶段
### Phase 0. 合法素材生产
- 只录“视频可播 + 数据已绑定”的比赛
- 自动巡检、自动录制、自动关闭结束窗口
- 自动素材过滤，Gold/Silver/Reject 分层

当前状态：进行中，主链可用

### Phase 1. 强事件识别
- 先不训练套利模型
- 先训练/验证“场内强事件识别模型”
- 重点标签：
  - 伤病事件
  - 红牌风险
  - 医疗暂停
  - 时间流失
  - 场面压制

当前状态：已落第一版强事件标签骨架

### Phase 2. 事件 + 盘口联合评测
- 给模型看比赛片段
- 同时给它对应时间窗口的盘口数据
- 让模型判断：
  - 是否会引发重定价
  - 向哪边变
  - 是否值得先手买入

当前状态：联合评测输入已生成；runner 第一版已实现并完成首轮 smoke test

### Phase 3. 套利机会评分器
- 输出不是比赛解说
- 输出是：
  - 是否值得出手
  - 第一腿买哪边
  - 预期赔率变化方向
  - 预期第二腿时机
  - 是否具备对冲空间

当前状态：schema 已落地，尚未开始 runner

### Phase 4. 训练 / 微调
- 不是默认立即做
- 只有当前三层稳定、样本量足够、benchmark 证明有必要时才进入
- 训练对象不是通用足球解说模型
- 训练对象是：
  - 直播事件 -> 盘口影响 -> 套利机会判断模型

当前状态：未开始

## 3. 当前已完成
### 录制与数据侧
- 录制主链稳定为只录“视频可播 + 数据已绑定”
- 每轮巡检重新比对当前全部直播
- 数据源健康检查已成为录制前硬门槛
- alias 学习已支持：
  - AI fallback 入库
  - 生产成功绑定写 learned
  - 多次命中自动 promote

### 素材侧
- 自动素材过滤脚本已完成
- Gold/Silver/Reject 长期素材库目录已建立
- Gold clip 自动切片已完成

### 分析与 benchmark
- 本地模型候选池已整理
- Round 1 benchmark 已完成
- Round 2 clip benchmark 已完成
- Round 3 scoreboard OCR benchmark 已完成
- Phase 2 联合评测 runner 已实现
- `Qwen3.5-VL-35B-A3B-4bit-MLX-CRACK` 已完成首轮联合评测 smoke test
- 当前结论：
  - OCR 受素材本身比分牌可见性影响很大
  - 暂时不能只靠 OCR 路线推进套利核心能力

### Phase 1 / Phase 2 数据结构
- 强事件标签 schema 已完成
- 套利评分 schema 已完成
- 联合评测 schema 已完成
- 强事件标签 skeleton 已生成
- 事件 + 盘口联合评测输入已生成

## 4. 当前产物位置
### 核心计划文档
- 总计划：
  [/Users/niannianshunjing/match_plan/docs/plans/2026-03-25-live-analysis-and-local-training-master-plan.md](/Users/niannianshunjing/match_plan/docs/plans/2026-03-25-live-analysis-and-local-training-master-plan.md)
- 模型候选池：
  [/Users/niannianshunjing/match_plan/docs/plans/2026-03-25-live-analysis-model-candidate-handbook.md](/Users/niannianshunjing/match_plan/docs/plans/2026-03-25-live-analysis-model-candidate-handbook.md)
- 强事件标签体系：
  [/Users/niannianshunjing/match_plan/docs/plans/2026-03-26-strong-event-label-taxonomy.md](/Users/niannianshunjing/match_plan/docs/plans/2026-03-26-strong-event-label-taxonomy.md)
- 联合评测计划：
  [/Users/niannianshunjing/match_plan/docs/plans/2026-03-26-event-odds-joint-evaluation-plan.md](/Users/niannianshunjing/match_plan/docs/plans/2026-03-26-event-odds-joint-evaluation-plan.md)
- 套利评分 schema 说明：
  [/Users/niannianshunjing/match_plan/docs/plans/2026-03-26-arbitrage-opportunity-scoring-schema.md](/Users/niannianshunjing/match_plan/docs/plans/2026-03-26-arbitrage-opportunity-scoring-schema.md)
- 素材过滤标准：
  [/Users/niannianshunjing/match_plan/docs/plans/2026-03-26-material-filtering-and-dataset-storage-standard.md](/Users/niannianshunjing/match_plan/docs/plans/2026-03-26-material-filtering-and-dataset-storage-standard.md)

### 数据集与样本
- 长期素材库：
  [/Volumes/990 PRO PCIe 4T/match_plan_dataset_library](/Volumes/990%20PRO%20PCIe%204T/match_plan_dataset_library)
- 强事件标签：
  [/Volumes/990 PRO PCIe 4T/match_plan_dataset_library/04_golden_samples/strong_event_labels](/Volumes/990%20PRO%20PCIe%204T/match_plan_dataset_library/04_golden_samples/strong_event_labels)
- 联合评测输入：
  [/Volumes/990 PRO PCIe 4T/match_plan_dataset_library/05_eval_sets/event_odds_joint_eval_inputs](/Volumes/990%20PRO%20PCIe%204T/match_plan_dataset_library/05_eval_sets/event_odds_joint_eval_inputs)

### 分析工作区
- 工作区入口：
  [/Users/niannianshunjing/match_plan/analysis_vlm/README.md](/Users/niannianshunjing/match_plan/analysis_vlm/README.md)

## 5. 当前推荐模型分工
- 在线主力候选：
  - `Qwen2.5-VL-7B-Instruct-4bit`
- 离线复核主力候选：
  - `Qwen3.5-VL-35B-A3B-4bit-MLX-CRACK`
- 外部候选：
  - `InternVL3-8B-MLX-4bit`
  - `InternVL3-38B-4bit`
- 暂不纳入第一轮主线：
  - `MiniCPM-V-4_5-int4`（当前 oMLX 兼容性未通过）

## 6. 当前进行中的事项
### 进行中 1：修复 watch 重复窗口/重复录制
当前状态：已修两层根因
- 录制恢复前校验 `watch_url`，避免刷新错 tab
- 自动巡检将把任何正在运行的录制 session 视为已占用比赛，避免同一场被双录

### 进行中 2：Phase 2 联合评测 runner
目标：
- 让模型同时看：
  - clip 关键帧
  - 盘口时间窗口摘要
- 输出：
  - `repricing_expected`
  - `repricing_direction`
  - `first_leg_side`
  - `first_leg_urgency`
  - `hedge_window_expected_sec`

当前状态：第一版已实现，三模型第一轮横向对比已完成；prompt v2 已验证有效

### 进行中 3：本地 oMLX 服务恢复与稳定化
目标：
- 保证联合评测、后续强事件 runner、模型对比都能持续调用本地 OMLX
- 避免因为本地服务中断把 benchmark 误判成模型失败

当前状态：
- 本轮已确认 `oMLX` 服务曾中断
- 当前已手动恢复 `127.0.0.1:8000`
- `/v1/models` 已重新可用
- 已新增显式控制脚本：
  - `/Users/niannianshunjing/match_plan/analysis_vlm/tools/omlx_server_ctl.py`
- 后续 benchmark 前后应统一用该脚本做状态检查与回收
- 已验证：
  - `status` 能识别真实 `python3 -m omlx.cli serve` 进程
  - `stop` 后端口与进程都会真正释放

## 7. 下一步
### 下一步 1
抽样复核联合评测输出，归纳三模型错误模式：
- 哪些是假阳性
- 哪些是方向判断错
- 哪些是第一腿方向错

### 下一步 2
开始补第一批高价值强事件人工标签，增强 Phase 2 ground truth

### 下一步 3
在更大样本集上复跑联合评测 prompt v2，确认当前结果不是小样本偶然

## 8. 当前阻塞与风险
- 当前 Gold 素材数量仍偏少
- 很多比赛虽然有直播，但没有同场盘口数据，不能进入训练/评测
- scoreboard OCR 受比分牌可见性影响大，暂时不应把它当主线能力
- 数据源偶发超时会影响 watch 触发，但当前已有健康检查和 fallback
- 本地 `oMLX` 服务如果未运行，会直接阻断 Phase 2/Phase 3 benchmark；后续需要把它作为固定前置检查

## 10. 本轮新增结果（2026-03-26 08:30）
- 本地 `oMLX` 服务已恢复，`/v1/models` 可正常访问
- Phase 2 联合评测 runner 首轮横向对比已完成：
  - `Qwen2.5-VL-7B-Instruct-4bit`
  - `Qwen3.5-VL-35B-A3B-4bit-MLX-CRACK`
  - `InternVL3-38B-4bit`
- 当前结论：
  - 第一版 prompt 下，35B 最有希望但假阳性明显
  - 收紧联合评测 prompt 并补充赔率 delta 后，三模型都提升到：
    - `repricing_expected_match_rate = 0.6667`
    - `repricing_direction_match_rate = 0.6667`
    - `first_leg_side_match_rate = 0.6667`
  - 当前仍优先：
    - 联合评测主力：`Qwen3.5-VL-35B-A3B-4bit-MLX-CRACK`
    - 快速基线：`Qwen2.5-VL-7B-Instruct-4bit`
  - `InternVL3-38B` 延迟依然太高
- 正式总结：
  - [/Users/niannianshunjing/match_plan/analysis_vlm/reports/2026-03-26-first-joint-eval-benchmark-summary.md](/Users/niannianshunjing/match_plan/analysis_vlm/reports/2026-03-26-first-joint-eval-benchmark-summary.md)

## 9. 更新规则
以后每次迭代都必须同步更新本文件，至少更新：
- 当前做到哪一阶段
- 本次新增了什么脚本/数据/报告
- 当前下一步是什么
- 当前已知阻塞是什么
