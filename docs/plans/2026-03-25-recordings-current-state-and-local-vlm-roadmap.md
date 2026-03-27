# recordings 当前项目计划与本地模型路线图

> 更新时间：2026-03-25 23:27 PDT  
> 适用范围：`/Users/niannianshunjing/match_plan/recordings`、`/Users/niannianshunjing/match_plan/live_dashboard`

## 1. 这份文档是做什么的

这份文档不是替代旧的“强事件驱动分析台”方案，而是把当前已经落地的录制/数据/巡检系统先讲清楚，再把下一阶段“训练本地模型看直播、理解数据、辅助分析”的路线补完整。

建议把它和下面这份旧方案一起看：

- [2026-03-23-live-handicap-strong-event-analysis-platform.md](/Users/niannianshunjing/match_plan/docs/plans/2026-03-23-live-handicap-strong-event-analysis-platform.md)

两者关系是：

- `2026-03-23` 方案：偏中长期目标，强调强事件检测、盘口分析、机会评分和分析台
- `2026-03-25` 方案：偏当前真实项目状态，强调已经跑通的录制、数据绑定、自动巡检和下一阶段本地模型落地路线

## 2. 当前项目真实架构

### 2.1 当前有效子项目

- [recordings](/Users/niannianshunjing/match_plan/recordings)
  - 真实浏览器会话录制
  - 数据面板绑定
  - 自动巡检与飞书回报
  - 同步 viewer 和左右并排分析视频

- [live_dashboard](/Users/niannianshunjing/match_plan/live_dashboard)
  - 本地实时数据快照服务
  - 本地网页面板
  - `latest.json` / `status.json`

- [docs/plans](/Users/niannianshunjing/match_plan/docs/plans)
  - 中长期方案文档

### 2.2 当前主链

当前主链已经从“单次手动录制脚本”扩成了三层：

1. 录制执行层
   - [run_auto_capture.py](/Users/niannianshunjing/match_plan/recordings/run_auto_capture.py)
   - 负责打开直播、等待起播、录制、抓数据、收尾

2. 自动巡检/调度层
   - [openclaw_recording_watch.py](/Users/niannianshunjing/match_plan/recordings/openclaw_recording_watch.py)
   - 负责每轮检查直播、数据健康、绑定、补位和启动录制

3. 通知与观察层
   - [openclaw_recording_watch_notify.py](/Users/niannianshunjing/match_plan/recordings/openclaw_recording_watch_notify.py)
   - [openclaw_recording_watch_status_notify.py](/Users/niannianshunjing/match_plan/recordings/openclaw_recording_watch_status_notify.py)
   - [openclaw_recording_progress.py](/Users/niannianshunjing/match_plan/recordings/openclaw_recording_progress.py)

## 3. 当前已经明确的业务规则

### 3.1 录制前提

当前项目已经统一成这条硬规则：

- 必须前端可打开并能真正播放
- 必须数据面板里有同一场比赛数据
- 视频和数据缺一不可
- 缺少任意一侧，都不录

换句话说：

- `可播 + 已绑定数据`：录
- `可播 + 未绑定数据`：不录
- `有数据 + 不可播`：不录

### 3.2 数据源优先级

当前数据源优先级已经统一成：

1. 直连 API（优先）
2. 本地 `live_dashboard`（fallback）

补充规则：

- 每次真正启动录制前，watch 必须先确认本轮数据快照健康且仍在更新
- 如果快照为空、过期、未同步好、或 fallback 到 dashboard 但 dashboard 还没暖起来，本轮直接跳过

### 3.3 自动巡检规则

当前持续任务推荐形态：

- 每 `2` 分钟巡检一次是否有新直播
- 每 `30` 分钟飞书汇报一次当前状态和桌面截图
- 场次不限制
- 有多少场“可播 + 已绑定数据”的直播，就录多少场
- 比赛结束后自动关闭对应 `watch` 窗口
- 保留 `schedules/live`

### 3.4 存储规则

当前视频产物规则已经定下来：

- `full.mp4`：主档
- `analysis_5m.mp4`：可选压缩分析副本
- 单段比赛：删除重复的 `seg_001`
- 多段比赛：保留必要分段

## 4. 当前已完成能力

### 4.1 已跑通

- Safari 真实直播打开、起播等待、录制、收尾
- 视频和数据绑定
- 自动巡检
- 录制中/录制结束通知
- 多显示器截图通知
- 比赛结束后自动关闭直播窗口
- `full.mp4 + sync_viewer + analysis side-by-side video`

### 4.2 已修过的关键 bug

- dashboard 单例范围互踩
- dashboard 重启后立即吃旧快照
- `--max-streams 0` 被吞掉
- 状态汇报器不会自动退出
- session 活跃判断过宽
- kickoff 时间被错误当成硬拦截条件
- 多个 session 收尾时误关彼此窗口
- 副屏截图太大导致飞书发送不稳定

## 5. 当前仍然存在的现实边界

### 5.1 数据源覆盖不完整

前端 `Ao vivo` 页面出现的比赛，不代表后台数据面板一定同步存在。

因此当前真实情况会出现：

- 前端有 20 场直播
- 其中只有一小部分能和数据面板对上
- 剩下的不是翻译错，而是数据源根本没给

所以“全录直播”不是当前目标，正确目标是：

- 只录“视频和数据一致”的比赛

### 5.2 多任务支持刚进入可用态

现在多个 watch 任务并存已经比以前安全很多，但仍建议：

- 多任务时优先让球种范围清晰
- 尽量避免无意义地同时创建很多高频巡检任务

### 5.3 时间的正确理解

当前项目里至少有三种时间：

- 电脑本地时间（美国）
- 飞书用户语义时间（默认按上海时间理解）
- 页面赛程时间（例如 `GMT-03:00`）

当前原则：

- 不修改 macOS 系统时间
- 飞书里的“今晚 10 点”等相对表达按上海时间理解
- 页面赛程时间只作弱参考，不作为硬拦截

## 6. 现阶段最重要的项目目标

当前阶段，不是继续堆更多录制技巧，而是把项目做成一条稳定的数据生产线：

1. 自动发现“可播 + 已绑定”的比赛
2. 自动录制和保存素材
3. 自动生成同步 viewer 和并排分析视频
4. 自动沉淀比赛视频、盘口数据、时间线、别名库
5. 为下一阶段本地模型训练准备高质量样本

## 7. 下一阶段：本地模型看直播并理解数据

这一阶段的目标，不是让模型直接下注，而是让它能够：

- 看直播画面
- 读取比分、比赛时间、牌、伤停、换人等事件
- 同步理解右侧数据面板
- 输出结构化 JSON
- 为后续强事件检测、复盘和策略研究提供稳定输入

### 7.1 这一阶段的核心原则

- 先做“稳定结构化理解”
- 再做“连续决策和事件评分”
- 不直接做下注执行

### 7.2 建议技术路线

结合当前项目状态和你给的补充意见，下一阶段推荐路线如下：

#### Step 1：单帧截图 + 本地 VLM 推理基线

目标：

- 先在本机跑通“截一帧 -> 模型看图 -> 输出 JSON”

建议：

- 截图：`mss` 或当前桌面录制窗口截图
- 模型：优先测试 `MLX` 和 `llama.cpp` 两条路
- 输出：只允许纯 JSON

最小输出字段：

- `score`
- `match_clock`
- `home_team`
- `away_team`
- `scene_type`
- `visible_event`
- `confidence`

#### Step 2：验证 JSON 稳定性

目标：

- 确认模型不会胡乱解释，不会输出一大段自然语言

硬规则：

- 系统提示词只允许 JSON
- JSON schema 尽量小
- 失败就记日志，不强行继续

#### Step 3：做“前缀缓存 + 增量图像”

你补充的这点很关键，后面应该明确采用：

- 固定系统提示词做 cache prefix
- 连续帧只输入变化部分

目标：

- 减少重复 prompt token
- 把连续帧延迟压到 `100~150ms`

#### Step 4：动态抽帧，而不是固定高频抽帧

推荐策略：

- 非关键场景：低频抽帧
- 场景变化大：提高频率
- 静止画面：直接跳过

建议方式：

- `FFmpeg scene change detection`
- 或基于像素差的轻量判定

#### Step 5：决策缓存

如果连续多帧结论几乎一致，就复用上一次结构化结果。

这一步的价值很高：

- 节省推理成本
- 降低延迟
- 减少模型抖动

#### Step 6：MLX vs llama.cpp A/B 测试

这一步不要凭印象决定，要在你这台机器上实测：

- 单帧延迟
- 连续帧延迟
- JSON 稳定率
- 显存/内存占用
- 长时间运行稳定性

#### Step 7：联动当前录制体系

最后才把本地模型接回当前项目：

- 直接看直播窗口
- 或看“视频 + 数据面板”的并排分析视频
- 输出结构化事件
- 存入时间线和样本库

## 8. 关于直播源延迟的关键提醒

这点必须单独记住：

- 模型推理快，不代表整个系统实时
- 直播源本身可能已经有几秒延迟

优先级建议：

- 屏幕直接截图：最适合当前项目
- WebRTC：理想，但当前不一定可控
- HLS：对“实时响应”不友好

所以在当前项目里，最现实的第一条路线是：

- **直接基于你已经打开的真实直播窗口截图**
- 不要先去折腾流媒体低延迟解码

## 9. 当前推荐的下一步任务顺序

建议从这个顺序开始：

1. 写单帧推理测试脚本
2. 验证纯 JSON 输出
3. 做连续帧缓存实验
4. 做动态抽帧
5. 做 MLX vs llama.cpp A/B
6. 把结果接回当前录制与分析视频流水线

## 10. 对 OpenClaw 和后续协作的要求

后面如果 OpenClaw 参与这条本地模型路线，应遵守：

- 先读这份文档，再读 `2026-03-23` 旧方案
- 当前项目目标不是下注，而是：
  - 高质量视频/数据样本生产
  - 稳定结构化理解
  - 强事件研究
- 任何模型实验都应优先产出：
  - 延迟数据
  - JSON 稳定率
  - 样本可复用性

## 11. 当前一句话结论

当前项目已经从“录视频脚本”升级成了“自动发现、只录已绑定比赛、自动回报、自动收尾、可继续向本地模型训练延伸的样本生产系统”。

下一阶段最重要的，不是继续堆更多录制逻辑，而是把本地模型看直播、读数据、稳定输出 JSON 这一条先跑通。
