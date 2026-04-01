# 2026-04-01 录制清理与“有视频但本地无数据”归档说明

## 本次处理

- 已永久清理此前隔离的明显垃圾录制 `188` 场。
- 重新整理出 `22` 场“有视频产物，但当前 session 本地无可用数据”的录制。
- 这 `22` 场已统一移动到：
  - `/Volumes/990 PRO PCIe 4T/match_plan_recordings/_video_without_local_data_20260401_092042`
- 目录内分为两类：
  - `recoverable_from_history_db`
  - `not_found_in_history_db`

## 目录与清单

- 归档根目录：
  - `/Volumes/990 PRO PCIe 4T/match_plan_recordings/_video_without_local_data_20260401_092042`
- 目录内说明：
  - `/Volumes/990 PRO PCIe 4T/match_plan_recordings/_video_without_local_data_20260401_092042/README.txt`
- 结构化清单：
  - `/Volumes/990 PRO PCIe 4T/match_plan_recordings/_video_without_local_data_20260401_092042/inventory.json`

## 分类结果

### 1. 可从总历史库回填的 `10` 场

这些录制当前本地 session 没有完整 `betting_data`，但能在总历史库
`/Users/niannianshunjing/match_plan/live_dashboard/live_service_data/history.db`
里找到对应比赛。

- `session_20260327_060834_059833`
- `session_20260327_081932_511752`
- `session_20260327_110724_499464`
- `session_20260327_120230_147968`
- `session_20260328_101015_083765`
- `session_20260328_174335_225190`
- `session_pgstapp_20260329_150029_Dominican_Republic_vs_Cu`
- `session_pgstapp_20260330_093027_Torpedo_Moskva_vs_Ska_kh`
- `session_pgstapp_20260330_093033_Netherlands_U21_vs_Belgi`
- `session_pgstapp_20260330_130013_Ind_Yumbo_vs_Union_Magda`

### 2. 总历史库也找不到的 `12` 场

这些录制先保留视频，不删除，后续如需人工复核或从其他来源补数据，可直接在归档目录中处理。

- `session_20260325_023219_355637`
- `session_20260325_040254_884048`
- `session_20260325_054211_969917`
- `session_20260325_054411_723868`
- `session_20260327_223318_822186`
- `session_20260328_004945_049344`
- `session_20260328_012952_250725`
- `session_20260328_030320_675229`
- `session_20260328_035805_654580`
- `session_pgstapp_20260331_195116_Canada_vs_Tunisia`
- `session_pgstapp_20260331_195116_Mexico_vs_Belgium`
- `session_pgstapp_20260331_200028_Iraq_vs_Bolivia`

## 说明

- 本次额外扫描后，没有发现还残留的“既没有视频、也没有数据”的 session，因此这一项新增删除数为 `0`。
- 这份说明只记录目录迁移和可追踪位置，不做回填。后续若要回填那 `10` 场，可直接基于 `inventory.json` 中记录的 `gid/ecid` 和行数继续处理。
