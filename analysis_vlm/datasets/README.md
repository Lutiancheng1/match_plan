# Dataset Rules

这里只放后续视觉理解、benchmark、评测和训练会用到的**合法素材定义**。

## 唯一合法入口

当前唯一合法样本入口是：

- `/Users/niannianshunjing/match_plan/recordings`
- 再经过：
  - `material_filter_pipeline.py`
  - `build_golden_sample_clips.py`

最终进入：

- `/Volumes/990 PRO PCIe 4T/match_plan_dataset_library/01_gold_matches`
- `/Volumes/990 PRO PCIe 4T/match_plan_dataset_library/04_golden_samples`

## 明确禁止

这些素材不允许进入 benchmark / eval / training：

- 无数据绑定录制
- `test_only` 录制
- timeline 覆盖率不够的录制
- `Silver Review` 在人工确认前的素材
- 人工随便塞进来的外部比赛视频

## 当前建议

第一轮 benchmark 只从 `Gold` 材料切出来的 clips 开始。
