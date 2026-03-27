# 素材过滤与长期存储标准

> 更新时间：2026-03-26  
> 录制素材根目录：`/Volumes/990 PRO PCIe 4T/match_plan_recordings`  
> 长期素材库根目录：`/Volumes/990 PRO PCIe 4T/match_plan_dataset_library`

## 1. 目标

这份文档用于定义：

- 什么素材是**真正合格**的，可反复打磨和长期留存
- 什么素材只能作为待复核
- 什么素材必须淘汰，不能进入样本、评测和训练链

当前结论：**不是所有 `bound` 素材都合格**，还必须通过时间覆盖率校验。

## 2. 严格过滤规则

一条素材只有同时满足下面条件，才算合格：

1. `status = completed`
2. `data_binding_status = bound`
3. `matched_rows > 0`
4. `full.mp4` 存在
5. `__timeline.csv` 存在
6. `__sync_viewer.html` 存在
7. **时间覆盖率 >= 0.95**

时间覆盖率定义：

`timeline_last_elapsed / video_duration_sec`

含义：

- 视频结束前，数据时间线必须基本跟到视频末尾
- 如果后面视频还很长，但 timeline 只覆盖前面一小段，这条素材不能进入黄金样本

## 3. 三档分类

### Gold（可直接长期留存）

满足全部严格规则，尤其是时间覆盖率 `>= 0.95`。

用途：

- 黄金样本
- 固定评测集
- 训练候选池
- 反复打磨使用

### Silver Review（待人工复核）

满足：

- `completed + bound + matched_rows > 0`
- 有 timeline 和 sync viewer
- 但时间覆盖率在 `0.60 ~ 0.95` 之间

用途：

- 作为复核候选
- 人工确认是否只截取前段可用窗口
- 不能直接进入黄金样本

### Reject（淘汰）

包含：

- unbound/test-only
- 没有 timeline
- 没有 sync viewer
- 时间覆盖率 < 0.60
- 视频后半段没有数据推进

这些素材：

- 不进入样本库
- 不进入评测集
- 不进入训练池

## 4. 当前盘点结果

- 扫描录制流总数：`55`
- Gold：`1`
- Silver Review：`2`
- Reject：`51`

### 当前 Gold 素材

- **Fortaleza FC x Deportivo Pasto**：覆盖率 `0.999`，匹配数据 `521`，视频 [FT_Fortaleza_FC_vs_Deportivo_Pasto__2026-03-24_19-26-12__full.mp4](/Volumes/990 PRO PCIe 4T/match_plan_recordings/2026-03-24/session_20260324_192612/FT_Fortaleza_FC_vs_Deportivo_Pasto_2026-03-24_19-26-12/FT_Fortaleza_FC_vs_Deportivo_Pasto__2026-03-24_19-26-12__full.mp4)

### 当前 Silver Review 候选（最佳代表）

- **Albion FC x Wanderers**：覆盖率 `0.622`，匹配数据 `47`，需人工判断是否截取局部窗口使用
- **Al-Arabi SC x Al Waab**：覆盖率 `0.665`，匹配数据 `45`，需人工判断是否截取局部窗口使用

## 5. 长期素材库目录

长期素材库根目录：`/Volumes/990 PRO PCIe 4T/match_plan_dataset_library`

- `00_docs`
- `01_gold_matches`
- `02_silver_review_queue`
- `03_rejected_materials`
- `04_golden_samples/clips`
- `04_golden_samples/labels`
- `04_golden_samples/meta`
- `05_eval_sets`
- `06_training_pool`
- `07_benchmarks`
- `08_model_outputs`
- `09_reviews`
- `10_manifests`

目录用途：

- `01_gold_matches`：记录严格合格比赛，不直接塞杂项
- `02_silver_review_queue`：待人工复核的素材
- `03_rejected_materials`：明确淘汰的素材索引
- `04_golden_samples`：后续真正切出来的 clip/label/meta
- `05_eval_sets`：固定评测集
- `06_training_pool`：训练候选池
- `10_manifests`：自动生成的全量清单和最佳素材索引

## 6. 自动化脚本

后续不再建议手工整理。请直接使用脚本：`/Users/niannianshunjing/match_plan/recordings/material_filter_pipeline.py`

示例：

```bash
python3 /Users/niannianshunjing/match_plan/recordings/material_filter_pipeline.py
```

脚本会自动：

- 扫描录制目录
- 刷新 Gold/Silver/Reject
- 更新 manifests
- 刷新长期素材库入口目录
- 重写这份过滤标准文档

## 7. 立即执行建议

- 现在不要直接训练。
- 先只从 **Gold** 素材里切第一批 clip。
- `Silver Review` 先人工复核，再决定是否局部截取使用。
- `Reject` 统一淘汰，不再混入后续流程。
