# 后续推进计划

> 创建时间: 2026-03-31
> 状态: 进行中

---

## 背景

- 9B base 模型已就绪 (score 96.9%, clock 98.8%), 不需要 LoRA
- LiveObserver 已就绪 (3.1-3.6s/帧)
- 录制管线已就绪 (App + pion_gst)
- 数据站账号因请求过快被封，正在获取新账号

---

## 第一优先: 账号恢复 + 防封控

### T1: 轮询间隔调优 (账号恢复前完成)

**问题**: 当前所有轮询都是 1 秒间隔，录制+看板同时跑时约 0.5s 一次请求，触发风控导致封号。

**方案**:
1. 用浏览器 DevTools 抓包数据站自身的请求间隔作为基准
2. 调整轮询间隔到安全范围 (预计 3-5 秒)
3. 加入随机抖动 (jitter)，避免固定频率特征

**需要改的文件**:
- `recordings/run_auto_capture.py` — `DATA_POLL_INTERVAL` (当前 1.0s)
- `live_dashboard/serve_live_dashboard.py` — `--interval` 默认值 (当前 1.0s)
- `recordings/poll_get_game_list.py` — 内部 sleep(0.35) 子请求间隔

**额外防护**:
- 录制和看板不要同时各自独立轮询，考虑共享同一个数据源
- 加 User-Agent 轮换或固定为浏览器 UA
- 错误响应 (CheckEMNU 等) 时指数退避，不要立即重试

### T2: 新账号接入

- 拿到新账号后更新 `recordings/live_dashboard.env`
- 验证 `transform.php?p=get_game_list` 返回正常 XML
- 确认 data_site_proxy 自动登录正常

---

## 第二优先: 录制管线修复

### T3: 修复 pgstapp 盘口数据中断

**问题**: 录制中途盘口数据轮询停止，视频继续但 betting_data.jsonl 断裂。

**影响**: 后续所有需要完整 timeline 的分析（联合评测、训练数据）都受影响。

**文件**: `recordings/pion_gst_direct_chain/run_pion_gst_direct_capture.py` + `recordings/run_auto_capture.py` (BettingDataPoller)

### T4: 录制完整 90 分钟比赛

- 当前最长录制只有 ~30 分钟半场
- 目标: 录制 5-10 场完整 90 分钟比赛 (含完整 timeline)
- 为联合评测和端到端验证提供数据基础

---

## 第三优先: 实时分析接入录制

### T5: 录制时实时模型分析

**目标**: 录制比赛的同时，让 9B 模型实时分析每一帧。

**架构**:
```
pion_gst 录制视频
    |
    v (每 N 秒抽一帧)
LiveObserver.observe_frame()
    |
    v
observation.json (score, clock, scene_type, events)
    |
    v
写入 session 目录，与 betting_data.jsonl 并行
```

**具体方案**:
- 在 `run_pion_gst_direct_capture.py` 中加入 observation 旁路
- 从 HLS 预览或归档段中定期抽帧 (每 3-5 秒一帧，匹配模型延迟)
- 调用 `LiveObserver.observe_frame()` 得到 observation dict
- 写入 `__observations.jsonl`，每条带时间戳
- 不阻塞主录制流程 (异步/独立线程)

**产出**:
- 每场比赛自动产出 observation 时间线
- 可直接用于联合评测、回放标注、训练数据补充

### T6: F3 联合评测 — 观察 x 盘口

**前置**: T4 (完整比赛) + T5 (实时分析)

**目标**: 验证 "看到进球/红牌 → 盘口变化" 之间的时间窗口

**方法**:
1. 对齐 observation 时间线和 betting_data 时间线
2. 找出 score 变化点 (observation) 与盘口跳变点 (betting_data) 的时间差
3. 统计: 模型识别事件到盘口完全重定价的平均窗口 (秒)
4. 判断这个窗口是否足够支撑人工套利决策

---

## 第四优先: 看板增强

### T7: Live Dashboard 接入 LiveObserver

**目标**: 在看板页面上实时展示模型 observation 结果。

**方案**:
- 看板已有 `latest.json` 数据流
- 新增 `observation` 字段到每场比赛卡片
- 展示: 模型识别的 score、clock、当前场景、近期事件

---

## 远期 (总计划 Phase 5-6)

### T8: 规则引擎 + 盘口联动

- 事件 → 规则判断 (红牌=少一人、伤退=换人名额) → 盘口重定价方向
- 强事件窗口检测: 事件发生到盘口完全重定价之间

### T9: 提醒与语音播报

- observation 稳定后才做
- 语音播报关键事件 + 套利机会提示
- 先文字提醒，再接 TTS

### T10: 训练蒸馏与模型迭代

- 用录制时产出的 observation 数据反哺训练
- 9B base 批量标注 → 35B 抽检纠错 → 迭代
- 只有当 9B base 出现系统性短板时才重启 LoRA

---

## 执行顺序

```
T1 轮询间隔调优 ← 现在就做
  → T2 新账号接入 ← 等用户拿到账号
    → T3 修复盘口数据中断
      → T4 录制完整 90min 比赛
        → T5 录制时实时分析 ← 关键里程碑
          → T6 联合评测
            → T7 看板增强
              → T8/T9/T10 远期
```

---

## 关键决策记录

| 决策 | 原因 |
|---|---|
| 不用 LoRA | 9B base 已 96.9% score, LoRA 反而降低 |
| 轮询间隔需要 >= 3s | 1s 轮询导致账号封禁 |
| 录制时实时分析 | 避免录完再离线分析的延迟，直接产出 observation 时间线 |
| 先不自动下注 | 当前阶段是人工套利辅助 |
