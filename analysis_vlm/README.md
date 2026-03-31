# Football Realtime Analysis Workspace

这个目录是后续“足球实时套利助手、本地模型评测、样本切片、知识库、训练准入”的统一工作区。

当前定位：

- **不是**录制主链
- **不是**自动录制调度层
- 是建立在 `/Users/niannianshunjing/match_plan/recordings` 合法足球产物之上的
  - 足球强事件识别
  - 角球 / 重启识别
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
## 当前项目真实目标

当前工作不是做通用“看球模型”，而是做：

- 训练足球实时观察员模型
- 先让模型看懂足球画面
- 再看懂足球规则
- 再看懂盘口变化
- 最后才看懂交易逻辑

当前阶段的真实路线是：

1. 先做足球知识库和任务定义
2. 再统一单帧 / 多帧 observation schema
3. 再补高质量教学样本
4. 再做“懂球”能力
5. 最后才进入规则、提醒和训练蒸馏
