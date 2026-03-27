# Match Plan

## 当前架构

这个目录现在承接两个核心子项目：

- [video_pipeline](/Users/niannianshunjing/match_plan/video_pipeline)
  足球比赛视频录制、截图、结构化数据采集、批次汇总。

- [live_dashboard](/Users/niannianshunjing/match_plan/live_dashboard)
  本地实时比赛看板，持续抓取源站数据并提供本地网页展示。

- [recordings](/Users/niannianshunjing/match_plan/recordings)
  真实浏览器直播录制、数据面板绑定、自动巡检、飞书回报、同步 viewer 和分析成片。

- [analysis_vlm](/Users/niannianshunjing/match_plan/analysis_vlm)
  本地视觉理解、模型候选池、benchmark、样本规范与训练准入工作区。
  - 现在也承载外部候选模型下载与接入脚本

## 当前建议阅读顺序

1. [video_pipeline/README.md](/Users/niannianshunjing/match_plan/video_pipeline/README.md)
2. [live_dashboard/README.md](/Users/niannianshunjing/match_plan/live_dashboard/README.md)
3. [recordings/README.md](/Users/niannianshunjing/match_plan/recordings/README.md)
4. [docs/plans](/Users/niannianshunjing/match_plan/docs/plans)
5. [analysis_vlm/README.md](/Users/niannianshunjing/match_plan/analysis_vlm/README.md)

## 项目目标

当前项目的真实目标不是“通用看球”，而是形成一个**事件驱动盘口套利研究闭环**：

1. `live_dashboard` 提供比赛列表、结构化状态和本地展示
2. `recordings` 负责真实直播录制、数据绑定、自动巡检、同步 viewer 和分析视频
3. `analysis_vlm` 负责：
   - 强事件识别
   - 事件 + 盘口联合评测
   - 套利机会评分 schema 与 benchmark
4. 在样本、知识库和评测成熟后，再决定是否进入训练/微调

## 当前状态

- `video_pipeline` 已经在本目录下重建
- `video_pipeline` 的赛程监听已切到 `PinchTab + attach Chrome` 常驻模式
- 旧路径兼容入口仍然存在：
  [Documents/LocalAI/video_pipeline](/Users/niannianshunjing/Documents/LocalAI/video_pipeline)
- `live_dashboard` 也已归档在本目录下
- `recordings` 已成为当前主要在维护的自动录制与数据绑定项目
- 当前计划文档建议先读：
  - [2026-03-26-live-analysis-execution-tracker.md](/Users/niannianshunjing/match_plan/docs/plans/2026-03-26-live-analysis-execution-tracker.md)
  - [2026-03-25-live-analysis-and-local-training-master-plan.md](/Users/niannianshunjing/match_plan/docs/plans/2026-03-25-live-analysis-and-local-training-master-plan.md)
  - [2026-03-26-strong-event-label-taxonomy.md](/Users/niannianshunjing/match_plan/docs/plans/2026-03-26-strong-event-label-taxonomy.md)
  - [2026-03-26-event-odds-joint-evaluation-plan.md](/Users/niannianshunjing/match_plan/docs/plans/2026-03-26-event-odds-joint-evaluation-plan.md)
  - [2026-03-26-arbitrage-opportunity-scoring-schema.md](/Users/niannianshunjing/match_plan/docs/plans/2026-03-26-arbitrage-opportunity-scoring-schema.md)
  - [2026-03-25-live-analysis-model-candidate-handbook.md](/Users/niannianshunjing/match_plan/docs/plans/2026-03-25-live-analysis-model-candidate-handbook.md)
  - [2026-03-25-recordings-current-state-and-local-vlm-roadmap.md](/Users/niannianshunjing/match_plan/docs/plans/2026-03-25-recordings-current-state-and-local-vlm-roadmap.md)
  - [2026-03-26-material-filtering-and-dataset-storage-standard.md](/Users/niannianshunjing/match_plan/docs/plans/2026-03-26-material-filtering-and-dataset-storage-standard.md)
  - [2026-03-23-live-handicap-strong-event-analysis-platform.md](/Users/niannianshunjing/match_plan/docs/plans/2026-03-23-live-handicap-strong-event-analysis-platform.md)
- 当前候选模型下载脚本：
  - [download_hf_model_to_omlx.py](/Users/niannianshunjing/match_plan/analysis_vlm/tools/download_hf_model_to_omlx.py)
- 当前 Phase 2 联合评测输入构建脚本：
  - [build_event_odds_joint_eval_inputs.py](/Users/niannianshunjing/match_plan/analysis_vlm/tools/build_event_odds_joint_eval_inputs.py)
- 当前 Phase 2 联合评测 runner：
  - [run_omlx_joint_eval_benchmark.py](/Users/niannianshunjing/match_plan/analysis_vlm/benchmarks/run_omlx_joint_eval_benchmark.py)

## 给接手 AI 的提示

- 先不要假设旧数据都还在
- 优先确认当前目录里的 README 和脚本
- 对 `sftraders.live` 相关逻辑，默认遵守“不要自动乱登录”的安全规则
