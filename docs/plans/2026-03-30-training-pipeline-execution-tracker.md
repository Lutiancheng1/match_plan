# 训练管线执行进度追踪

> 文档创建: 2026-03-30
> 最后更新: 2026-03-30
> 状态: Phase A-F2 完成。9B base + JSON 后处理已上线，LiveObserver 端到端验证通过

---

## 总目的

构建"数据飞轮"闭环: 用 35B 模型生成种子标注 -> 微调 9B 模型 -> 9B 批量标注新数据 -> 35B 抽检纠错 -> 迭代提升。

最终目标: **Qwen3.5-VL-9B 成为实时直播分析主力模型** — 延迟低、精度够、能跑在 Mac 上实时出结果，支撑"规则判断 -> 盘口分析 -> 交易决策"链路。

> 2026-03-30 决策: 微调目标从 Qwen2.5-VL-7B 升级为 Qwen3.5-VL-9B（早期融合 VL、更强 OCR、混合 SSM 架构）

---

## 数据资产概览

| 资产 | 数量 | 说明 |
|---|---|---|
| Gold 比赛 | 58 场 (39 场 >=45s) | 含 25 场 pgstapp 新录制 |
| Frame observation (训练) | 2,434 条 (全部预标注完成) | 01_frame_observation/records/ |
| Clip observation (训练) | 746 条 (预标注中) | 02_clip_observation/records/ |
| Rule teaching (训练) | 765 条 | 03_rule_teaching/records/ |
| Holdout eval (永不训练) | 640 条 | 400 frames + 120 clips + 120 rules |
| Holdout 比赛 | 5 场 | Fortaleza, Colombia vs France, Real Madrid W vs Barcelona W, Botafogo PB vs ASA, Gubbio vs Ravenna |

---

## 模型角色

| 模型 | 角色 | 延迟 | 用途 |
|---|---|---|---|
| Qwen3.5-VL-9B-8bit-MLX-CRACK | 在线主力 (微调目标) | ~66 tok/s | 实时分析、批量标注 |
| Qwen3.5-VL-4B-JANG_4S-CRACK | 超快 worker | 极快 | 批量标注备选 |
| ~~Qwen2.5-VL-7B-Instruct-4bit~~ | ~~旧在线主力~~ | - | 已废弃，由 3.5-VL-9B 替代 |
| Qwen3.5-VL-35B-A3B-4bit | 离线审核 | ~2s/帧 | 种子标注、质量抽检 |
| Qwen3.5-VL-122B-A10B-4bit | 教师 | ~8s/帧 | 疑难样本裁判 |

---

## 执行阶段与进度

### Phase A: 数据质量提升 (自动标注 + 人工复核)

#### A1: 自动预标注 Frame Observation
- **状态**: ✅ 完成
- **脚本**: `analysis_vlm/tools/auto_prelabel_frames.py`
- **模型**: Qwen3.5-VL-35B-A3B-4bit-MLX-CRACK
- **总量**: 2,434/2,434 帧 (100%)
- **实际耗时**: ~90 分钟 (含 oMLX 重启补跑)
- **开始时间**: 2026-03-30 07:32
- **完成时间**: 2026-03-30 09:30

#### A2: 自动预标注 Clip Observation
- **状态**: 执行中
- **脚本**: `analysis_vlm/tools/auto_prelabel_clips.py`
- **模型**: Qwen3.5-VL-35B-A3B-4bit-MLX-CRACK
- **总量**: 746 clips
- **预估耗时**: ~60 分钟
- **测试结果**: 3/3 成功, 0 错误

#### A3: 预标注质量抽检
- **状态**: ✅ 通过
- **方法**: 随机抽样 50 frame + 3 clip, 检查错误率
- **通过标准**: 错误率 < 15%
- **最终结果**: Frame 错误率 **2.0%** (1/50 scene_type=unknown), Clip 错误率 0% — **PASSED**
- **脚本**: `analysis_vlm/tools/sample_prelabel_audit.py`

---

### Phase B: Holdout 基线建立

#### B1: Holdout 基线 Benchmark
- **状态**: ✅ 7B 基线已完成, 35B 待跑
- **脚本**: `analysis_vlm/benchmarks/run_holdout_eval.py`
- **评测集**: 400 holdout frames
- **7B 基线 (Qwen2.5-VL-7B-Instruct-4bit)**:
  - JSON valid rate: **97.25%**
  - Score 提取率: **8.75%** ← 关键弱项, 微调重点
  - Clock 提取率: **44.0%**
  - Avg latency: 3,928 ms
  - 报告: `analysis_vlm/reports/Qwen2.5-VL-7B-Instruct-4bit__holdout_eval__1774881362/`
- **9B 基线 (Qwen3.5-VL-9B-8bit-MLX-CRACK)**:
  - JSON valid rate: **99.75%** (+2.5% vs 7B)
  - Score 提取率: **39.25%** (+30.5% vs 7B!!!)
  - Clock 提取率: **40.0%** (-4% vs 7B)
  - Avg latency: 5,608 ms (与 clip 预标注并行, 有竞争)
  - 报告: `analysis_vlm/reports/Qwen3.5-VL-9B-8bit-MLX-CRACK__holdout_eval__1774888212/`
- **35B 基线**: 待执行

---

### Phase C: 第一轮 LoRA 微调

#### C1: 构建 MLX LoRA 训练数据集
- **状态**: 执行中 (随 A1 同步增长)
- **脚本**: `analysis_vlm/tools/build_training_dataset.py`
- **当前**: 2,384 条 (2,381 frame + 3 clip)
- **输出**: `06_training_pool/training_data/frame_conversations.jsonl`
- **格式**: conversation JSONL (system + user[image+prompt] + assistant[JSON])

#### C2: LoRA 微调
- **状态**: ✅ 完成
- **脚本**: `analysis_vlm/training/run_mlx_lora_finetune.py` (mlx-vlm trainer API)
- **基座**: Qwen3.5-VL-9B-8bit-MLX-CRACK
- **参数**: LoRA rank=16, alpha=0.1, lr=1e-5, iters=1000, batch_size=1, grad_checkpoint=true
- **训练数据**: 2,381 frame examples + 400 holdout eval
- **训练结果**:
  - Train loss: 1.855 → **0.00003** (持续下降, 无反弹)
  - Val loss: 2.869 → **0.000** (完全收敛)
  - Speed: ~1.5 it/sec, ~50 tok/sec
  - Peak memory: 11.3 GB
  - 总耗时: ~15 分钟
- **Adapter**: `analysis_vlm/training/adapters/football_obs_vlora_20260330_102325/`
- **Checkpoints**: 200/400/600/800/1000 iters

#### C3: 训练后 Holdout 评测
- **状态**: ✅ 完成
- **脚本**: `analysis_vlm/benchmarks/run_holdout_eval_lora.py` (mlx-vlm 本地推理)
- **结果对比**:

| 指标 | 7B (Qwen2.5) | 9B Base | 9B + LoRA |
|---|---|---|---|
| JSON valid | 97.25% | 99.75% | **100.0%** |
| Score 提取率 | 8.75% | 39.25% | 33.25% |
| Clock 提取率 | 44.0% | 40.0% | 39.75% |
| Latency | 3,928ms | 5,608ms | 6,877ms |

- **分析**: LoRA 显著提升了 JSON 格式合规性 (100%)，但 Score/Clock 提取率未改善
- **原因**: 训练数据中大量 score/clock 为空字符串，模型学会了保守输出
- **改进方向**: 第二轮需要增加含清晰比分的样本比例，或用 35B 补标 score/clock

---

### Phase D: 迭代改进

#### D1: Holdout 错误分析
- **状态**: ✅ 完成
- **前置工作**: 用 35B 对 400 holdout frames 做自动预标注作为 ground truth (400/400, 0 errors)
- **脚本**: `analysis_vlm/tools/analyze_holdout_errors.py`
- **结果对比** (vs 35B ground truth):

| 错误类型 | 9B Base | 9B + LoRA | 说明 |
|---|---|---|---|
| scene_type_mismatch | 44 (11.0%) | 51 (12.8%) | LoRA 略差 |
| score_missed | 141 (35.3%) | **165 (41.3%)** | LoRA 更差 — 训练数据空 score 过多 |
| score_wrong | 6 (1.5%) | 5 (1.3%) | 基本持平 |
| clock_missed | 138 (34.5%) | 138 (34.5%) | 完全一致 |
| clock_wrong | 3 (0.8%) | 3 (0.8%) | 完全一致 |
| json_invalid | 1 (0.25%) | **0 (0%)** | LoRA 唯一提升 |

- **关键发现**:
  1. **Score 提取是最大瓶颈** — 35B 在 holdout 中读出 298/400 (74.5%) 的比分，9B 只读出 157/400 (39.2%)，差距 141 帧
  2. LoRA 让 score 更差 (157→133) — 但训练数据实际有 85.8% 非空 score，说明问题是 **9B 视觉能力不够**，不是训练数据缺失
  3. **replay→live_play** 是最常见场景混淆 (各 12 例)
  4. 存在拼写错误输出: `crow_or_bench`, `celeation` — 需在 prompt 或后处理中修复
- **数据密度验证**:
  - 训练 JSONL: 2381 条，85.8% 有非空 score，85.6% 有非空 clock — 数据不缺
  - 问题确认: 9B 的 OCR/视觉分辨能力 < 35B，纯 LoRA 不足以弥补

---

### Phase E: 数据清理与模型能力再评估

#### E1: 35B 幻觉发现与数据清理
- **状态**: ✅ 完成
- **发现**: 35B 在无记分牌帧上幻觉输出 prompt 示例值 `score=1-0, clock=67:14`
  - 训练数据: 388/2434 (14.3%) 被污染
  - Holdout GT: 136/400 被污染
- **修复**:
  - 检测 `clock=67:14` 作为幻觉标记，清零 score/clock/visibility
  - 修改所有 prompt 中的 `67:14` → `45:00` 防止再次触发
  - 重建训练 JSONL: 2369 条, score 密度 69.9%, 幻觉归零
- **脚本**: `analysis_vlm/benchmarks/run_scoreboard_ocr_benchmark.py`

#### E2: 第二轮 LoRA 微调 + 公平评测
- **状态**: ✅ 完成
- **训练**: 2369 frame (clean), 1000 iters, loss 1.826→0.00003
- **Adapter**: `analysis_vlm/training/adapters/football_obs_vlora_20260330_184947/`
- **公平评测结果** (vs 干净 GT, 162 帧有记分牌):

| 模型 | JSON 合法 | Score 提取 | Clock 提取 |
|---|---|---|---|
| **9B base (无 LoRA)** | 99.8% | **96.9%** (157/162) | **98.8%** |
| 9B+LoRA v1 (脏数据) | 100.0% | 82.1% (133/162) | 98.8% |
| 9B+LoRA v2 (清理数据) | 99.0% | 76.5% (124/162) | 98.1% |

- **关键结论**:
  1. **9B base 已经非常强** — 有记分牌时 score 96.9%, clock 98.8%
  2. **LoRA 微调有害** — 两轮都降低了 score 提取能力 (96.9%→82.1%→76.5%)
  3. 之前看到的"低 score 提取率"是 35B ground truth 幻觉造成的假象
  4. LoRA 唯一贡献是 JSON 格式 100%，但 base 已经 99.8%，不值得为 0.2% 牺牲 score
- **决策**: 放弃 LoRA，直接用 9B base + 轻量后处理 (JSON 修复) 即可

### Phase F: 修正后的推进计划

#### F1: JSON 后处理层 (高优先, 简单)
- **状态**: ✅ 完成
- **目标**: 解决 9B base 0.2% JSON 无效问题
- **交付**: `analysis_vlm/lib/observation_postprocess.py`
  - markdown 代码块剥离
  - 截断 JSON 修复 (闭合括号)
  - enum 规范化 + 类型强制转换
  - `parse_model_output(text) -> dict` — 永远返回合法 observation，不会抛异常

#### F2: 实时管线集成 (高优先)
- **状态**: ✅ 完成
- **目标**: 9B base 直接接入实时直播分析链路
- **交付**: `analysis_vlm/lib/live_observer.py`
  - `LiveObserver` 类: `observe_frame(path)`, `observe_bytes(bytes)`, `health_check()`
  - 内置 F1 后处理，输出始终为合法 observation dict
  - 端到端测试通过: 3 场 holdout 比赛, score/clock 正确提取
  - 延迟: ~3.1-3.6s/帧 (oMLX 9B-8bit)

#### F3: 联合评测 — 观察×盘口 (中优先)
- **目标**: 验证 frame observation 能否辅助盘口判断
- 9B base score 96.9% 已达标

#### F4: 扩大数据飞轮 (低优先)
- 用 9B base 批量标注新比赛
- 35B 抽检 (注意幻觉问题)
- 训练数据扩充备用

---

## 依赖链

```
✅ A1 Frame 预标注 — 2434/2434 (100%)
✅ A2 Clip 预标注 — 746/746 (100%)
✅ A3 质量抽检 — PASSED (2.0%)
✅ B1 7B 基线 — JSON 97.25%, Score 8.75%
✅ B1 9B 基线 — JSON 99.75%, Score 39.25%
✅ C1 训练数据集 — 3,127 条 (2381 frame + 746 clip)
✅ C2 LoRA 微调 — loss 1.855→0.00003, 1000 iters
✅ C3 Holdout 评测 — JSON 100%, Score 33.25%
✅ 模型下载 — 9B + 4B 已下载并验证

✅ D1 错误分析 — score_missed 是最大瓶颈 (35-41%), LoRA 让 score 更差
✅ E1-E2 — 35B 幻觉发现, 清洗数据, 9B base score 96.9% 已足够强
✅ 清理 2.5-VL-7B 引用 (完成)
✅ F1 JSON 后处理层 — observation_postprocess.py
✅ F2 LiveObserver — live_observer.py, 端到端验证通过

→ F3 联合评测 — 观察×盘口 (下一步)
→ F4 数据飞轮扩展 (低优先)
```

---

## 关键脚本清单

| 脚本 | 用途 | 状态 |
|---|---|---|
| `analysis_vlm/tools/auto_prelabel_frames.py` | Frame 自动预标注 | ✅ 2434/2434 |
| `analysis_vlm/tools/auto_prelabel_clips.py` | Clip 自动预标注 | ✅ 746/746 |
| `analysis_vlm/tools/sample_prelabel_audit.py` | 预标注质量抽检 | ✅ PASSED (2.0%) |
| `analysis_vlm/tools/build_training_dataset.py` | 生成训练 JSONL | ✅ 3,127 条 |
| `analysis_vlm/tools/build_eval_dataset.py` | 生成验证 JSONL | ✅ 400 条 |
| `analysis_vlm/benchmarks/run_holdout_eval.py` | Holdout 评测 (oMLX) | ✅ 7B + 9B 完成 |
| `analysis_vlm/benchmarks/run_holdout_eval_lora.py` | Holdout 评测 (LoRA) | ✅ 9B+LoRA 完成 |
| `analysis_vlm/tools/analyze_holdout_errors.py` | 错误分析 | ✅ 完成 |
| `analysis_vlm/training/run_mlx_lora_finetune.py` | LoRA 微调 (mlx-vlm) | ✅ 完成 (已废弃) |
| `analysis_vlm/training/merge_adapter.py` | Adapter 合并 | 已废弃 (LoRA 有害) |
| `analysis_vlm/lib/observation_postprocess.py` | JSON 后处理 | ✅ F1 交付 |
| `analysis_vlm/lib/live_observer.py` | 实时观察客户端 | ✅ F2 交付 |

---

## 验收标准

1. A1 完成后: 随机 10 条 frame record, observation 字段已填充且 JSON 合法
2. B1 完成后: holdout 报告包含 2 模型 x 400 frame 的指标表
3. C2 完成后: 训练 loss 持续下降, 验证 loss 不大幅上升
4. C3 完成后: 微调后 score/clock 提取率明显高于基线
5. 端到端: 微调后模型跑完整 Gold 比赛, JSON 合法率 > 95%
