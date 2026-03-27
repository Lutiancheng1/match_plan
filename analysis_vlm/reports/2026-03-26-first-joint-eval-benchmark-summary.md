# Phase 2 联合评测第一轮横向对比

日期：2026-03-26  
评测集：`/Volumes/990 PRO PCIe 4T/match_plan_dataset_library/05_eval_sets/event_odds_joint_eval_inputs/manifests/current_event_odds_joint_eval_manifest.json`  
记录数：`24`

## 第一版 prompt 的初始结果

### 1. Qwen2.5-VL-7B-Instruct-4bit
- run id:
  - `/Users/niannianshunjing/match_plan/analysis_vlm/reports/Qwen2.5-VL-7B-Instruct-4bit__joint_eval__1774539647`
- `json_valid_rate = 1.0`
- `repricing_expected_match_rate = 0.0`
- `repricing_direction_match_rate = 0.6667`
- `first_leg_side_match_rate = 0.6667`
- `avg_latency_ms = 5412.08`

### 2. Qwen3.5-VL-35B-A3B-4bit-MLX-CRACK
- run id:
  - `/Users/niannianshunjing/match_plan/analysis_vlm/reports/Qwen3.5-VL-35B-A3B-4bit-MLX-CRACK__joint_eval__1774539794`
- `json_valid_rate = 1.0`
- `repricing_expected_match_rate = 0.3333`
- `repricing_direction_match_rate = 0.0833`
- `first_leg_side_match_rate = 0.2917`
- `avg_latency_ms = 4788.12`

### 3. InternVL3-38B-4bit
- run id:
  - `/Users/niannianshunjing/match_plan/analysis_vlm/reports/InternVL3-38B-4bit__joint_eval__1774539918`
- `json_valid_rate = 1.0`
- `repricing_expected_match_rate = 0.0833`
- `repricing_direction_match_rate = 0.6667`
- `first_leg_side_match_rate = 0.6667`
- `avg_latency_ms = 17282.62`

初始结论：

- `Qwen3.5-VL-35B` 在 `repricing_expected` 上相对更有希望，但整体仍有明显假阳性。
- `Qwen2.5-VL-7B` 和 `InternVL3-38B` 更偏保守或模板化输出。

## prompt v2 收紧后的正式结果

这轮后续又做了一版更严格的联合评测 prompt，并把赔率窗口 `t_plus_0 -> t_plus_60` 的数值变化量显式加入输入。核心约束是：

- `repricing_expected` 必须输出 `true/false`
- 没有明确强事件、且赔率窗口变化不显著时，必须输出 `false`
- 不允许仅凭“主队压制/末段时间”这种泛化理由就输出 `true`

### 1. Qwen2.5-VL-7B-Instruct-4bit
- run id:
  - `/Users/niannianshunjing/match_plan/analysis_vlm/reports/Qwen2.5-VL-7B-Instruct-4bit__joint_eval__1774542832`
- `json_valid_rate = 1.0`
- `repricing_expected_match_rate = 0.6667`
- `repricing_direction_match_rate = 0.6667`
- `first_leg_side_match_rate = 0.6667`
- `avg_latency_ms = 5197.21`

### 2. Qwen3.5-VL-35B-A3B-4bit-MLX-CRACK
- run id:
  - `/Users/niannianshunjing/match_plan/analysis_vlm/reports/Qwen3.5-VL-35B-A3B-4bit-MLX-CRACK__joint_eval__1774542724`
- `json_valid_rate = 1.0`
- `repricing_expected_match_rate = 0.6667`
- `repricing_direction_match_rate = 0.6667`
- `first_leg_side_match_rate = 0.6667`
- `avg_latency_ms = 4046.16`

### 3. InternVL3-38B-4bit
- run id:
  - `/Users/niannianshunjing/match_plan/analysis_vlm/reports/InternVL3-38B-4bit__joint_eval__1774542973`
- `json_valid_rate = 1.0`
- `repricing_expected_match_rate = 0.6667`
- `repricing_direction_match_rate = 0.6667`
- `first_leg_side_match_rate = 0.6667`
- `avg_latency_ms = 10857.93`

## 当前正式结论

- prompt v2 对联合评测效果提升明显，说明当前阶段更值得继续优化输入与约束，而不是立刻训练。
- 三个模型在当前这份 24 条评测集上都达到相同的匹配率，说明当前数据规模还偏小，模型差异尚未完全拉开。
- 在这种情况下，**延迟和稳定性**就是更重要的选择因子。
- 当前最合适的联合评测主力仍然是：
  - `Qwen3.5-VL-35B-A3B-4bit-MLX-CRACK`
- 当前最合适的快速基线是：
  - `Qwen2.5-VL-7B-Instruct-4bit`
- `InternVL3-38B-4bit` 当前仍然过慢，不适合常规联合评测主力。

## 当前推荐

- Phase 2 联合评测主力候选：
  - `Qwen3.5-VL-35B-A3B-4bit-MLX-CRACK`
- Phase 2 联合评测快速基线：
  - `Qwen2.5-VL-7B-Instruct-4bit`
- 当前不推荐作为联合评测主力：
  - `InternVL3-38B-4bit`

## 下一步

1. 抽样复核 `results.csv`，按错误类型分：
   - 假阳性
   - 方向错
   - 第一腿方向错
2. 开始补第一批高价值强事件人工标签，给 Phase 2 提供更可信的 ground truth。  
3. 在更大样本集上复跑 prompt v2，验证当前 `0.6667` 是否只是小样本偶然。  
