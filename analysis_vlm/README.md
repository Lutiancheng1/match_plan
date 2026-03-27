# Analysis VLM Workspace

这个目录是后续“事件驱动盘口套利分析、本地模型评测、样本切片、知识库、训练准入”的统一工作区。

当前定位：

- **不是**录制主链
- **不是**自动录制调度层
- 是建立在 `/Users/niannianshunjing/match_plan/recordings` 合法产物之上的
  - 强事件识别
  - 事件 + 盘口联合评测
  - benchmark
  - 样本整理
  - 知识库建设
  - 训练准入评估

## 目录约定

- `datasets/`
  - 数据集说明、来源说明、合法样本约束
- `schemas/`
  - 模型输出 JSON schema
- `benchmarks/`
  - benchmark 配置、评测任务清单、结果汇总模板
- `registry/`
  - 模型候选池与角色分工
- `reports/`
  - benchmark 报告、对比结论、阶段性结论

## 固定规则

- 只允许使用“视频可播 + 数据已绑定”的比赛素材
- 不从无数据绑定录制中切样本
- 当前 `/Volumes/990 PRO PCIe 4T/match_plan_dataset_library/01_gold_matches` 是第一优先合法素材源
- `Silver Review` 只能人工确认后再进入评测/训练池
- 在没有固定 benchmark 和评测集之前，不进入微调

## 当前第一步

先做三件事：

1. 用 `registry/model_registry.json` 维护模型候选池
2. 用 `schemas/live_frame_observation.schema.json` 固定第一版纯 JSON 输出
3. 用 `benchmarks/benchmark_plan.json` 固定第一轮 benchmark 任务
4. 用 `benchmarks/run_omlx_probe.py` 先验证本地模型接口、文本模型与视觉模型是否能真实跑通
5. 用 `tools/download_hf_model_to_omlx.py` 把外部候选模型稳定下载到 `~/.omlx/models`
6. 用 `benchmarks/run_omlx_benchmark.py` 对 Gold clips 跑第一轮批量 benchmark
7. 用 `benchmarks/run_omlx_joint_eval_benchmark.py` 对“事件 + 盘口”联合评测记录跑 Phase 2 benchmark
8. 用 `tools/omlx_server_ctl.py` 显式管理本地 oMLX 服务，避免 benchmark 结束后留下一堆不受控进程

## 当前模型探测结论

截至 2026-03-26，已经完成一轮 OMLX 真实视觉 probe：

- `InternVL3-8B-MLX-4bit`
  - 可用
  - 单图 probe 约 `13.95s`
- `InternVL3-38B-4bit`
  - 可用
  - 单图 probe 约 `14.03s`
- `MiniCPM-V-4_5-int4`
  - 当前被 OMLX 识别，但视觉请求返回 `HTTP 500`
  - 暂不纳入第一轮 benchmark

详细记录见：

- `/Users/niannianshunjing/match_plan/analysis_vlm/reports/2026-03-26-omlx-model-probe-results.md`

## 当前项目真实目标

当前工作不是做通用“看球模型”，而是做：

- 识别直播中的**重定价事件**
- 结合盘口数据判断这些事件是否会引起重定价
- 最终形成**套利机会评分**

所以当前阶段的真实路线是：

1. 先做强事件识别
2. 再做事件 + 盘口联合评测
3. 再做套利机会评分
4. 最后才判断是否训练/微调

## 当前已落地的第一步实施件

第一步已经不是纸面计划，而是可执行工具：

- 强事件标签骨架生成脚本：
  - `/Users/niannianshunjing/match_plan/analysis_vlm/tools/build_strong_event_label_skeletons.py`

它会自动：

- 读取当前 Gold clip manifest
- 读取每个 clip 已有的 `label` 和 `meta`
- 生成独立的 `strong_event_labels` 目录
- 为每个 clip 输出：
  - 强事件标注骨架
  - 联合评测 stub
  - 套利评分 stub
  - 比分 / 比赛时间 / 盘口快照 bootstrap 信息

当前默认输出目录：

- `/Volumes/990 PRO PCIe 4T/match_plan_dataset_library/04_golden_samples/strong_event_labels`

当前总清单：

- `/Volumes/990 PRO PCIe 4T/match_plan_dataset_library/04_golden_samples/strong_event_labels/manifests/current_strong_event_label_manifest.json`

示例：

```bash
python3 /Users/niannianshunjing/match_plan/analysis_vlm/tools/build_strong_event_label_skeletons.py
```

这一步的作用是：

- 不破坏原始 `labels/*.json`
- 单独建立“强事件识别 -> 事件+盘口联合评测 -> 套利评分”的新标注层

相关正式计划文档：

- `/Users/niannianshunjing/match_plan/docs/plans/2026-03-25-live-analysis-and-local-training-master-plan.md`
- `/Users/niannianshunjing/match_plan/docs/plans/2026-03-26-strong-event-label-taxonomy.md`
- `/Users/niannianshunjing/match_plan/docs/plans/2026-03-26-event-odds-joint-evaluation-plan.md`
- `/Users/niannianshunjing/match_plan/docs/plans/2026-03-26-arbitrage-opportunity-scoring-schema.md`

对应 schema：

- `/Users/niannianshunjing/match_plan/analysis_vlm/schemas/strong_event_observation.schema.json`
- `/Users/niannianshunjing/match_plan/analysis_vlm/schemas/event_odds_repricing_eval.schema.json`
- `/Users/niannianshunjing/match_plan/analysis_vlm/schemas/arbitrage_opportunity_score.schema.json`

## 当前第二个实施件

第二个实施件也已经落成可执行脚本：

- 事件 + 盘口联合评测输入构建脚本：
  - `/Users/niannianshunjing/match_plan/analysis_vlm/tools/build_event_odds_joint_eval_inputs.py`

它会自动：

- 读取当前 `strong_event_labels` manifest
- 对每个 clip 读取：
  - `source_meta_path`
  - `source_timeline`
  - `pivot_elapsed`
- 自动构建事件前后赔率窗口：
  - `t_minus_15`
  - `t_plus_0`
  - `t_plus_15`
  - `t_plus_30`
  - `t_plus_60`
- 生成 Phase 2 联合评测记录：
  - `event_context`
  - `odds_windows`
  - `joint_eval_ground_truth`
  - `joint_eval_model_target`
  - `joint_eval_annotation`

当前默认输出目录：

- `/Volumes/990 PRO PCIe 4T/match_plan_dataset_library/05_eval_sets/event_odds_joint_eval_inputs`

当前总清单：

- `/Volumes/990 PRO PCIe 4T/match_plan_dataset_library/05_eval_sets/event_odds_joint_eval_inputs/manifests/current_event_odds_joint_eval_manifest.json`

示例：

```bash
python3 /Users/niannianshunjing/match_plan/analysis_vlm/tools/build_event_odds_joint_eval_inputs.py
```

这一步的作用是：

- 不直接改动 `strong_event_labels/*.json`
- 单独产出一层可复跑的 Phase 2 联合评测输入
- 让后面的规则系统、模型评测和人工复核都使用同一份赔率窗口 Ground Truth

## 当前第三个实施件

第三个实施件现在已经开始落地：

- Phase 2 联合评测 runner：
  - `/Users/niannianshunjing/match_plan/analysis_vlm/benchmarks/run_omlx_joint_eval_benchmark.py`

它会：

- 从联合评测 manifest 中读取记录
- 从每个 clip 抽 3 张关键帧并拼成 contact sheet
- 把该 clip 对应的赔率窗口摘要一并喂给模型
- 输出：
  - `repricing_expected`
  - `repricing_direction`
  - `repricing_strength`
  - `first_leg_side`
  - `first_leg_urgency`
  - `hedge_window_expected_sec`

示例：

```bash
python3 /Users/niannianshunjing/match_plan/analysis_vlm/benchmarks/run_omlx_joint_eval_benchmark.py \
  --model Qwen3.5-VL-35B-A3B-4bit-MLX-CRACK \
  --limit 4
```

## 当前联合评测第一轮结论

截至 2026-03-26，Phase 2 联合评测 runner 已完成第一轮横向对比：

- `Qwen2.5-VL-7B-Instruct-4bit`
- `Qwen3.5-VL-35B-A3B-4bit-MLX-CRACK`
- `InternVL3-38B-4bit`

当前总结：

- 三个模型的 JSON 合法率都稳定在 `1.0`
- 在第一版 prompt 下，`Qwen3.5-VL-35B` 在 `repricing_expected` 上最有希望
- 收紧 prompt 并显式加入赔率 delta 后，三模型在当前 24 条小样本上都提升到 `0.6667`
- 当前仍然推荐：
  - 联合评测主力：`Qwen3.5-VL-35B-A3B-4bit-MLX-CRACK`
  - 快速联合评测基线：`Qwen2.5-VL-7B-Instruct-4bit`
  - `InternVL3-38B` 延迟明显过高，当前不适合做联合评测主力

正式报告：

- `/Users/niannianshunjing/match_plan/analysis_vlm/reports/2026-03-26-first-joint-eval-benchmark-summary.md`

## 本地 oMLX 服务控制

为了避免 benchmark 时临时起服务、忘记回收，当前建议统一用：

- `/Users/niannianshunjing/match_plan/analysis_vlm/tools/omlx_server_ctl.py`

示例：

```bash
python3 /Users/niannianshunjing/match_plan/analysis_vlm/tools/omlx_server_ctl.py status
python3 /Users/niannianshunjing/match_plan/analysis_vlm/tools/omlx_server_ctl.py start
python3 /Users/niannianshunjing/match_plan/analysis_vlm/tools/omlx_server_ctl.py stop
```

## 当前第一轮正式 benchmark 结论

已完成首轮 Gold clips 正式 benchmark，总结见：

- `/Users/niannianshunjing/match_plan/analysis_vlm/reports/2026-03-26-first-formal-benchmark-summary.md`

当前推荐：

- 在线主力：
  - `Qwen2.5-VL-7B-Instruct-4bit`
- 离线复核主力：
  - `Qwen3.5-VL-35B-A3B-4bit-MLX-CRACK`
- 保留观察：
  - `InternVL3-8B-MLX-4bit`
  - `InternVL3-38B-4bit`
- 暂不纳入：
  - `MiniCPM-V-4_5-int4`

## 当前第二轮 clip benchmark 结论

已完成第二轮 clip/contact-sheet benchmark，总结见：

- `/Users/niannianshunjing/match_plan/analysis_vlm/reports/2026-03-26-round2-clip-benchmark-summary.md`

当前结论：

- 第二轮 runner 已可用
- contact-sheet 输入比直接多图更稳
- 但当前模型在：
  - `score_detected`
  - `match_clock_detected`
  上还不够强
- 所以下一步优先是：
  - 调 prompt
  - 调输入布局
  - 补标签骨架
- 还**不是**进入训练的时机

## 当前第三轮 scoreboard OCR 结论

已完成第一轮 scoreboard OCR 辅助验证，总结见：

- `/Users/niannianshunjing/match_plan/analysis_vlm/reports/2026-03-26-round3-scoreboard-ocr-summary.md`

并已完成扩大的正式第三轮对比，总结见：

- `/Users/niannianshunjing/match_plan/analysis_vlm/reports/2026-03-26-round3-formal-scoreboard-benchmark-summary.md`

当前结论：

- “只看顶部记分牌/时间区域”的输入策略比前两轮更有前景
- 这一步就是让模型重点看**场上的比分牌和比赛时间条**
- 但当前 `Gold clips` 本身大多**没有稳定可见的比分牌/时间条**
- 所以第三轮正式 benchmark 的主要发现是：
  - 先要补 `scoreboard-visible` 样本筛选
  - 再继续做 OCR benchmark
- 当前阶段**还没有把投注/盘口数据直接喂给模型**
- 投注/盘口数据目前只作为：
  - 标签来源
  - 对照基准
  - 评测参考

也就是说，当前还在做纯视觉 baseline，不做“视觉 + 盘口联合输入”。

## 当前第三轮扩展结论

已完成扩大的正式第三轮对比，总结见：

- `/Users/niannianshunjing/match_plan/analysis_vlm/reports/2026-03-26-round3-formal-scoreboard-benchmark-summary.md`

这轮最关键的发现不是“哪个模型 OCR 最强”，而是：

- 当前 `Gold clips` 大多**没有稳定可见的比分牌/时间条**
- 所以第三轮 OCR benchmark 的首要问题是样本输入，而不是模型本身
- 下一步应优先补：
  - `scoreboard-visible` 样本筛选
  - 强事件标签
  - 事件 + 盘口联合评测

## 当前可直接用的本地 probe

脚本：

- `/Users/niannianshunjing/match_plan/analysis_vlm/benchmarks/run_omlx_probe.py`

用途：

- 直连本地 `omlx` OpenAI 兼容接口
- 探测文本模型
- 探测视觉模型
- 给 benchmark 之前做最小可用性确认

## 当前可直接用的第一版 benchmark runner

脚本：

- `/Users/niannianshunjing/match_plan/analysis_vlm/benchmarks/run_omlx_benchmark.py`

用途：

- 读取 Gold clip manifest
- 自动抽每个 clip 的第一帧
- 通过本地 `omlx` 批量调用视觉模型
- 记录：
  - 延迟
  - token 用量
  - JSON 合法率
  - 原始输出
- 生成 `results.csv` 和 `summary.json`

示例：

```bash
python3 /Users/niannianshunjing/match_plan/analysis_vlm/benchmarks/run_omlx_benchmark.py \
  --model InternVL3-8B-MLX-4bit \
  --manifest "/Volumes/990 PRO PCIe 4T/match_plan_dataset_library/04_golden_samples/meta/current_golden_sample_manifest.json" \
  --output-dir /Users/niannianshunjing/match_plan/analysis_vlm/reports \
  --limit 5
```

## 当前可直接用的第二轮 clip benchmark runner

脚本：

- `/Users/niannianshunjing/match_plan/analysis_vlm/benchmarks/run_omlx_clip_benchmark.py`

用途：

- 对每个 Gold clip 抽多张关键帧
- 一次性让模型综合判断：
  - `scene_type`
  - `score_detected`
  - `match_clock_detected`
  - `event_candidates`
  - `confidence`
- 自动和现有标签骨架对比：
  - `score_exact_match`
  - `clock_exact_match`
  - `clock_minute_match`

示例：

```bash
python3 /Users/niannianshunjing/match_plan/analysis_vlm/benchmarks/run_omlx_clip_benchmark.py \
  --model Qwen2.5-VL-7B-Instruct-4bit \
  --manifest "/Volumes/990 PRO PCIe 4T/match_plan_dataset_library/04_golden_samples/meta/current_golden_sample_manifest.json" \
  --output-dir /Users/niannianshunjing/match_plan/analysis_vlm/reports \
  --limit 4
```

## 当前可直接用的第三轮 scoreboard OCR benchmark runner

脚本：

- `/Users/niannianshunjing/match_plan/analysis_vlm/benchmarks/run_omlx_scoreboard_crop_benchmark.py`

用途：

- 从每个 clip 抽多张关键帧
- 只裁顶部记分牌/时间区域
- 拼成 scoreboard contact sheet
- 专门评测：
  - `score_detected`
  - `match_clock_detected`

示例：

```bash
python3 /Users/niannianshunjing/match_plan/analysis_vlm/benchmarks/run_omlx_scoreboard_crop_benchmark.py \
  --model Qwen3.5-VL-35B-A3B-4bit-MLX-CRACK \
  --manifest "/Volumes/990 PRO PCIe 4T/match_plan_dataset_library/04_golden_samples/meta/current_golden_sample_manifest.json" \
  --output-dir /Users/niannianshunjing/match_plan/analysis_vlm/reports \
  --limit 4
```

说明：

- 当前 runner 已加入 `tesseract` 顶部裁剪可见性预检
- 默认会记录 `scoreboard_visible`
- 如需强制忽略可见性门槛继续观察模型保守输出，可加：
  - `--skip-visibility-gate`

## 当前可直接用的模型下载脚本

脚本：

- `/Users/niannianshunjing/match_plan/analysis_vlm/tools/download_hf_model_to_omlx.py`

用途：

- 直接把 Hugging Face 候选模型下载到 `~/.omlx/models`
- 优先适配当前本机的 `omlx` 本地模型目录
- 当前默认镜像使用 `https://hf-mirror.com`
- 适合下载：
  - `openbmb/MiniCPM-V-4_5-int4`
  - `mlx-community/InternVL3-8B-MLX-4bit`
  - `mlx-community/InternVL3-38B-4bit`

示例：

```bash
python3 /Users/niannianshunjing/match_plan/analysis_vlm/tools/download_hf_model_to_omlx.py \
  openbmb/MiniCPM-V-4_5-int4 \
  --target-name MiniCPM-V-4_5-int4
```

## 相关文档

- 总计划：
  `/Users/niannianshunjing/match_plan/docs/plans/2026-03-25-live-analysis-and-local-training-master-plan.md`
- 模型候选池手册：
  `/Users/niannianshunjing/match_plan/docs/plans/2026-03-25-live-analysis-model-candidate-handbook.md`
- 素材过滤标准：
  `/Users/niannianshunjing/match_plan/docs/plans/2026-03-26-material-filtering-and-dataset-storage-standard.md`
