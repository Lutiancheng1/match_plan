# Live Match Video Recording + Betting Data Capture

基于真实浏览器会话的直播录制 + 秒级投注数据采集系统。

## 一句话说明

从真实浏览器的 `sftraders.live/schedules/live` 页面读取 `Ao vivo` 直播列表
→ 用户选择比赛
→ 在新窗口打开对应 `watch` 页面
→ 等视频真正开始播放
→ `ffmpeg` 屏幕录制 + 每秒投注数据采集
→ 分段保存
→ 输出视频和对齐后的数据结果。

## 当前状态

当前脚本已经可以用于真实启动，但它不是“无人值守自动登录前端站点”的方案。

- 前端视频侧: 依赖你已经登录好的真实 Chrome / Safari 会话
- 数据侧: 录制/watch 默认优先使用 `live_dashboard.env` 直连 API；本地 dashboard 作为 fallback 和本地观察面板
- 录制侧: 依赖 macOS 屏幕录制权限、浏览器自动化权限和 `ffmpeg`

当前实测状态:

- `Chrome` 前端录制链已跑通
- `Safari` 前端录制链已跑通，已实际完成 1 路 `watch` 窗口打开、起播等待、1 分钟录制、分段和合并
- 浏览器数据会话直连目前仍然主要依赖 `Chrome CDP`；`Safari` 模式下录制/watch 默认直接走 `.env` 直连 API，本地 dashboard 只做 fallback

换句话说，现在可用的链路是:

1. 你手动登录真实浏览器里的 `sftraders.live`
2. 你把页面停在 `schedules/live`，并确保 `Ao vivo` 页签能看到正在直播的比赛
3. 如果 Chrome 已开启 CDP，脚本会优先从浏览器会话里捕获真实投注请求
4. 如果 Chrome CDP 不可用，脚本会优先走 `live_dashboard.env` 直连 API，本地 dashboard 仅作为 fallback
5. 脚本再去打开新的 `watch` 窗口、等播放开始、排布窗口、启动录制和数据采集

它目前不做的事情:

- 不绕过 Cloudflare 或其他反自动化校验
- 不负责自动完成 `sftraders.live` 前端登录
- 不要求你在浏览器里再额外登录 `hga035.com`

## 定时巡检与自动录制

现在项目已经额外提供一层“watch job”外壳，用于 OpenClaw/Feishu 远程巡检指定比赛并自动开录。

新增入口：

- 目标规则配置：`/Users/niannianshunjing/match_plan/recordings/watch_targets.json`
- 巡检触发器：`/Users/niannianshunjing/match_plan/recordings/openclaw_recording_watch.py`
- 进度回报器：`/Users/niannianshunjing/match_plan/recordings/openclaw_recording_progress.py`
- 带截图的巡检摘要发送器：`/Users/niannianshunjing/match_plan/recordings/openclaw_recording_watch_notify.py`
- 长期状态回报器：`/Users/niannianshunjing/match_plan/recordings/openclaw_recording_watch_status_notify.py`
- 长期巡检守护器：`/Users/niannianshunjing/match_plan/recordings/recording_watch_supervisor.py`

默认策略：

- 长期目标规则来自 `watch_targets.json`
- OpenClaw 消息里可以临时 `override` 追加或替换目标
- 默认每 `10` 分钟巡检一次
- watch 会把本地 `live_dashboard` 的 `GTYPES` 自动同步成当前任务的球种范围：
  - 如果任务是全量不筛选，dashboard 也切到全 sport
  - 如果任务只筛某些球种，dashboard 也同步切到对应球种
- 如果启用 `openclaw_recording_watch_notify.py`，每次巡检摘要会直接附带一张“当前桌面截图”
- 如果检测到多显示器，会分别发送主屏和副屏截图；单屏时只发送主屏
- 巡检截图默认使用 JPG，降低飞书发送失败的概率；多屏时逐张发送
- 如果需要“持续巡检 + 独立半小时状态汇报”，应同时运行：
  - `openclaw_recording_watch.py` 负责每轮发现/补位/启动录制
  - `openclaw_recording_watch_status_notify.py` 负责每 `30` 分钟发送一次当前整体状态和桌面截图
- 命中目标后，优先且默认只录有数据绑定的比赛
- 当前默认配置下，如果比赛未绑定到面板数据，则直接跳过，不启动录制
- 每次真正启动录制前，watch 调度层必须先确认本轮实时数据快照可用且仍在更新；如果数据源健康检查失败、`snapshot_rows=0`、或快照未取到，则本轮即使前端出现直播也不启动录制
- 数据源策略是：直连 API 优先；如果本轮直连快照超时，而本地 dashboard 快照仍然新鲜可用，则自动回退到 dashboard 快照继续绑定和判定
- 每一轮巡检都必须重新比对“当前仍在直播列表里的全部比赛”，不能只处理首次出现的新比赛
- `active_locks` 只用于防止已经在录的同一场比赛被重复开录；它不能阻止“前几轮未绑定、后几轮才拿到盘口数据”的比赛重新尝试绑定和启动
- 除了 `active_locks`，watch 还必须把“当前任何正在运行的录制 session 里已经选中的比赛”视为已占用；即使那场录制是手动触发的，也不能被后续自动巡检再次启动，避免同一场比赛被双录
- 录制恢复时，必须先校验当前绑定的浏览器标签页 `watch_url` 仍然属于这一场比赛；如果标签页已经漂移到别的比赛，禁止直接刷新旧页，必须改为按该流自己的 `watch_url` 重新打开，避免刷错比赛或制造重复窗口
- 录制恢复在按 `watch_url` 重开之前，必须先清掉同一 `watch_url` 的旧残留页面，避免同一场比赛越恢复越堆出多个重复窗口
- 比赛结束后，会自动关闭该场对应的 `watch` 直播窗口，并保留 `schedules/live`
- 只有在用户明确要求时，才允许打开“测试流”模式去录无数据比赛，并且必须明确标记为测试流
- 如果某一轮一开始就发现多场直播，会按 `max_streams` 一次性拉满
- 如果最初只录了部分路数，后续巡检发现新直播时，也会继续自动补开，直到达到 `max_streams` 上限
- 如果 `max_streams = 0`，表示不限路数：当前有多少场已绑定直播，就录多少场
- 同一场比赛不会重复开；去重粒度是“比赛签名”，不是整个目标规则
- 如果 watch 配置把 `progress_interval_minutes` 设成 `0`，则只保留 watch cron 自己的定时回报，不再额外启动单独的 progress notifier
- 录制中的进度每 `30` 分钟回报一次
- 进度消息默认带“当前录制桌面”的截图
- 重大异常会即时上报，不等半小时：
  - 起播等待超时
  - 连续黑屏/卡顿恢复失败并放弃该路
  - 所有录制流都已失败并提前终止

### 当前推荐的长期运行方式

长期巡检不要再用单纯的 `nohup` 野跑。

当前推荐的稳定结构是：

- `openclaw_recording_watch.py`
  - 真正做“发现直播 / 绑定数据 / 启动录制 / 补位”
- `openclaw_recording_watch_status_notify.py`
  - 每 `30` 分钟发一次状态和截图
- `recording_watch_supervisor.py`
  - 负责 `start / ensure-running / status / stop / restart`
  - 维护 pid / state / restart 逻辑
- OpenClaw cron job
  - 不直接承担录制逻辑
  - 只定时执行 supervisor 的 `ensure-running`

也就是说，最稳的不是“纯 OpenClaw job”，而是：

- **OpenClaw job 管 supervisor**

这样做的好处：

- 任务在 OpenClaw 里是正式可见的
- 以后能统一检查 / 停掉 / 重启
- 真正长期跑的还是项目自己的守护层，稳定性比单纯 agent turn 更好

### 默认配置文件结构

`watch_targets.json` 现在分成两层：

- `defaults`
- `targets`

其中：

- `defaults` 决定浏览器、运动类型、默认时长、默认巡检间隔、默认进度回报间隔、默认飞书目标等
- `targets` 只定义“想抓什么比赛”，不负责调度

每个 target 可用字段：

- `id`
- `name`
- `enabled`
- `priority`
- `gtypes`
- `league_keywords`
- `team_keywords`
- `match_query`
- `allow_test_recording`
  - 当前默认值为 `false`
  - 只有用户明确允许时，才改成 `true`
- `duration_minutes`
- `max_streams`
- `progress_interval_minutes`
- `max_streams`
  - `0` 表示不限路数
  - 大于 `0` 时表示上限路数

匹配规则：

- `gtypes` 先筛运动类型
- `league_keywords` 按联赛关键词做包含匹配
- `team_keywords` 按队名关键词做包含匹配
- `match_query` 按整场比赛文本做额外匹配
- 如果多个条件同时存在，则按“都满足”处理

### 巡检触发器怎么用

单次巡检：

```bash
python3 /Users/niannianshunjing/match_plan/recordings/openclaw_recording_watch.py --check-once
```

单次巡检并直接把“摘要 + 当前桌面截图”发到飞书：

```bash
python3 /Users/niannianshunjing/match_plan/recordings/openclaw_recording_watch_notify.py \
  --config /Users/niannianshunjing/match_plan/recordings/watch_targets.json \
  --job-id default_watch \
  --browser safari \
  --output-root '/Volumes/990 PRO PCIe 4T/match_plan_recordings' \
  --interval-minutes 10 \
  --max-streams 4 \
  --progress-interval-minutes 0 \
  --channel feishu \
  --target oc_d8caa357cf6943f7a0b2917a2488876a \
  --account legacy
```

持续巡检：

```bash
python3 /Users/niannianshunjing/match_plan/recordings/openclaw_recording_watch.py --loop
```

持续巡检 + 独立半小时状态回报：

```bash
python3 /Users/niannianshunjing/match_plan/recordings/openclaw_recording_watch.py \
  --config /Users/niannianshunjing/match_plan/recordings/watch_targets_all_live_bound_only.json \
  --job-id all_live_bound_continuous \
  --browser safari \
  --output-root '/Volumes/990 PRO PCIe 4T/match_plan_recordings' \
  --interval-minutes 2 \
  --loop \
  --max-streams 0 \
  --progress-interval-minutes 30
```

```bash
python3 /Users/niannianshunjing/match_plan/recordings/openclaw_recording_watch_status_notify.py \
  --job-id all_live_bound_continuous \
  --interval-minutes 30 \
  --channel feishu \
  --target oc_d8caa357cf6943f7a0b2917a2488876a \
  --account legacy \
  --screenshot-scope desktop \
  --loop
```

推荐的 supervisor 入口：

```bash
python3 /Users/niannianshunjing/match_plan/recordings/recording_watch_supervisor.py start \
  --job-id all_live_bound_continuous \
  --config /Users/niannianshunjing/match_plan/recordings/watch_targets_all_live_bound_only.json \
  --browser safari \
  --output-root '/Volumes/990 PRO PCIe 4T/match_plan_recordings' \
  --interval-minutes 2 \
  --progress-interval-minutes 30 \
  --max-streams 0 \
  --channel feishu \
  --target oc_d8caa357cf6943f7a0b2917a2488876a \
  --account legacy \
  --screenshot-scope desktop
```

日常巡检保活入口：

```bash
python3 /Users/niannianshunjing/match_plan/recordings/recording_watch_supervisor.py ensure-running \
  --job-id all_live_bound_continuous \
  --config /Users/niannianshunjing/match_plan/recordings/watch_targets_all_live_bound_only.json \
  --browser safari \
  --output-root '/Volumes/990 PRO PCIe 4T/match_plan_recordings' \
  --interval-minutes 2 \
  --progress-interval-minutes 30 \
  --max-streams 0 \
  --channel feishu \
  --target oc_d8caa357cf6943f7a0b2917a2488876a \
  --account legacy \
  --screenshot-scope desktop
```

说明：

- 第一条命令负责每 `2` 分钟检查一次当前前端直播，发现“可播且已绑定数据”的比赛就直接录制
- 第二条命令不负责发现比赛，只负责每 `30` 分钟汇总当前 watch 状态，并发送主屏/副屏截图
- 如果你只想要“每轮巡检都带截图”，才用 `openclaw_recording_watch_notify.py`
- 如果你要的是“2 分钟检查，但 30 分钟才发一次状态”，推荐 `watch.py + watch_status_notify.py` 这一对组合
- 如果你要的是“长期稳定跑，并且以后要让 OpenClaw 正式管理它”，推荐 `OpenClaw job + recording_watch_supervisor.py`

持续巡检，但到指定时间自动停止：

```bash
python3 /Users/niannianshunjing/match_plan/recordings/openclaw_recording_watch.py \
  --loop \
  --stop-at "2026-03-25 23:00"
```

持续巡检，但最多运行一段时间后自动停止：

```bash
python3 /Users/niannianshunjing/match_plan/recordings/openclaw_recording_watch.py \
  --loop \
  --max-runtime-minutes 180
```

临时追加一个目标关键词：

```bash
python3 /Users/niannianshunjing/match_plan/recordings/openclaw_recording_watch.py \
  --check-once \
  --override-match-query "Arsenal W vs Chelsea W"
```

只按消息里的临时目标巡检，不用配置文件里的目标：

```bash
python3 /Users/niannianshunjing/match_plan/recordings/openclaw_recording_watch.py \
  --check-once \
  --override-match-query "Arsenal W vs Chelsea W" \
  --override-replace
```

### 自动截止停止规则

现在 watch 脚本已经支持两种“自动停掉巡检任务”的方式：

- `--stop-at "YYYY-MM-DD HH:MM"`
  - 按本机本地时间，到这个绝对时间后停止 watcher
- `--max-runtime-minutes N`
  - 从启动时开始计时，运行满 `N` 分钟后停止 watcher

如果两个参数同时给出：

- 脚本会取更早的那个时间点作为实际截止点

注意：

- 停止的是“巡检 watcher”，不是去强杀已经启动中的录制子任务
- 也就是说：
  - 到截止时间后，不会再发起新的录制
  - 已经开始的单轮录制，会按自己的 session 正常收尾

### 触发后的行为

watch 脚本命中目标后，不直接自己拼录制逻辑，而是：

1. 写出本轮显式选中的 `watch_selected_matches.json`
2. 调用 `openclaw_recording_launcher.py`
3. 由 launcher 再启动：
   - `run_auto_capture.py`
   - `openclaw_recording_progress.py`

这样做的好处是：

- 主录制链保持不重写
- 进度回报、最终总结、输出目录仍然统一
- session 目录里会多出 watch runtime 元数据，便于排查

### 动态补路规则

watch 场景里，`max_streams` 既是“单轮最多新开多少路”，也是“总活跃录制上限”。

## 素材过滤与长期素材库

训练、评测和后续本地模型打磨，**只能使用“视频可播 + 数据已绑定 + 时间覆盖率合格”的素材**。

当前长期素材库根目录：

- `/Volumes/990 PRO PCIe 4T/match_plan_dataset_library`

过滤标准文档：

- `/Users/niannianshunjing/match_plan/docs/plans/2026-03-26-material-filtering-and-dataset-storage-standard.md`

自动刷新脚本：

- `/Users/niannianshunjing/match_plan/recordings/material_filter_pipeline.py`
- `/Users/niannianshunjing/match_plan/recordings/build_golden_sample_clips.py`

默认严格规则：

- `status = completed`
- `data_binding_status = bound`
- `matched_rows > 0`
- 存在 `full.mp4`
- 存在 `__timeline.csv`
- 存在 `__sync_viewer.html`
- `timeline_last_elapsed / video_duration_sec >= 0.95`

三档分类：

- `Gold`：可直接进入长期留存、评测和训练候选池
- `Silver Review`：基本可用，但时间覆盖率不足，需要人工复核
- `Reject`：无数据绑定、无同步产物、或时间覆盖率明显不足，直接淘汰

直接刷新一次素材库：

```bash
python3 /Users/niannianshunjing/match_plan/recordings/material_filter_pipeline.py
```

可调参数示例：

```bash
python3 /Users/niannianshunjing/match_plan/recordings/material_filter_pipeline.py \
  --recordings-root '/Volumes/990 PRO PCIe 4T/match_plan_recordings' \
  --dataset-root '/Volumes/990 PRO PCIe 4T/match_plan_dataset_library' \
  --gold-threshold 0.95 \
  --silver-threshold 0.60
```

脚本会自动：

1. 扫描录制目录中的 `session_result.json`
2. 计算每条素材的视频时长、timeline 行数和时间覆盖率
3. 刷新 `Gold / Silver Review / Reject` 三档索引
4. 更新长期素材库目录和每场素材入口目录
5. 更新 manifests 和过滤标准文档

从当前 Gold 素材自动切第一批可复用 clip：

```bash
python3 /Users/niannianshunjing/match_plan/recordings/build_golden_sample_clips.py
```

默认行为：

1. 只读取 `01_gold_matches/current_gold_matches.json`
2. 围绕盘口/比分变化自动切事件 clip
3. 额外补少量稳定参考片段（calm clips）
4. 输出到：
   - `04_golden_samples/clips`
   - `04_golden_samples/labels`
   - `04_golden_samples/meta`
5. 自动写：
   - `current_golden_sample_manifest.json`
   - 每条 clip 的 `label.json` 骨架
   - 每条 clip 的 `meta.json`

当前行为：

- 如果当前已有 `1` 路在录
- 下次巡检又发现新的直播
- 只要总活跃路数还没到 `max_streams`
- watcher 就会继续补开新的比赛
- 直到达到总上限

当前不会发生：

- 不会因为目标规则相同，就把后续新比赛全部挡掉
- 不会把同一场比赛重复开两次

当前限流与去重方式：

- 限流：按总活跃路数
- 去重：按比赛签名

### watch/runtime 元数据

自动巡检场景下，session 目录里会额外看到：

- `watch_selected_matches.json`
- `watch_runtime.json`

这些文件里会记录：

- `watch_job_id`
- `trigger_reason`
- `target_match_rule_source`
- `trigger_mode`
- `session_lock_metadata`
- `progress_snapshots`
- `final_notify`

`session_result.json` 在录制完成后也会同步带上：

- `watch`
- `progress`

## 存储策略

当前正式存储策略已经调整为：

- `full.mp4` 是主档，始终保留
- `analysis_5m.mp4` 是可选压缩副本，默认不生成
- `seg_001 / seg_002 / ...` 只在多段或必要场景保留

## 比赛数据绑定与 AI 别名学习

当前比赛与实时数据的绑定顺序是：

1. 先用现有规则和本地别名库匹配
2. 再用联赛别名和文本标准化做二次匹配
3. 只有当前批次仍然 `unbound` 的比赛，才会触发一次 AI 批量翻译 fallback

本地别名文件：

- 队名别名：`/Users/niannianshunjing/match_plan/recordings/team_aliases.json`
- 联赛别名：`/Users/niannianshunjing/match_plan/recordings/league_aliases.json`
- 队名 learned 计数：`/Users/niannianshunjing/match_plan/recordings/team_alias_learned.json`
- 联赛 learned 计数：`/Users/niannianshunjing/match_plan/recordings/league_alias_learned.json`

AI fallback 的原则：

- 不在每次巡检热路径里对所有比赛都调用模型
- 只对当前批次里常规规则没绑上的比赛一次性批量翻译
- 会同时翻译前端比赛名和联赛名，再和数据快照里的名称做对比
- 一旦帮助成功绑定，会把中英别名写回本地库
- 后续遇到相同或相近名称时，直接走本地库，不再重复调用模型
- 高频且已确认正确的中英映射应直接固化到本地别名库，不要只依赖临时 AI 翻译
  - 例如国家队与友谊赛高频写法：`Vietnam <-> 越南`、`Bangladesh <-> 孟加拉国`、`International Friendly <-> 国际友谊赛`
- 生产环境里一旦成功绑定，也会自动写入 learned 计数文件
- 同一对映射累计命中达到 `2` 次后，会自动 promote 到稳定别名库

当前实现使用：

- OpenClaw 自定义 OpenAI 兼容模型接口
- 优先从 `~/.openclaw/openclaw.json` 的 `custom` provider 读取 `baseUrl` 和 `apiKey`
- 运行时先探测 `/models`，再选择合适的非 MiniMax 文本模型
- 当前实测最稳定的翻译模型是 `glm-5`

这层 AI 只作为“最后一层补位”：

- 常规规则能命中时，不调用模型
- 只有未命中的那一批比赛，才会一次性翻译
- 这样能减少巡检延迟，也避免把模型翻译放到每轮全量匹配里

已验证的一个典型例子：

- 前端：`Kazakhstan vs Namíbia`
- 数据侧：`哈萨克斯坦 vs 纳米比亚`
- 联赛：`FIFA Series / Friendlies` ↔ `国际友谊赛`

这类中英文名称现在可以被学习并写回本地别名库，后续再遇到时不需要重新翻译。

具体规则：

### 1. 单段比赛

如果一场比赛最终只有一个有效视频分段，并且 `full.mp4` 只是对这个单段的直接整理结果：

- 保留 `full.mp4`
- 删除重复的 `seg_001`
- 保留 `manifest.json`、数据文件、时间线、viewer 等非视频产物

这样可以避免“同一段视频存两份”导致空间翻倍。

### 2. 多段比赛

如果比赛被切成多段，或者中途出现卡顿/断流补段：

- 保留 `full.mp4`
- 保留必要分段
- 保留 `manifest.json`

原因是：

- `full.mp4` 便于统一查看、同步 viewer、时间线对齐
- 原始分段仍然是问题排查和局部重处理的重要依据

### 3. 可选 analysis 副本

可以额外生成一个用于浏览或次级分析的压缩副本：

- 文件名：`__analysis_5m.mp4`
- 编码：`HEVC / H.265`
- 目标码率：`5 Mbps`

默认不生成，只有在显式开启时才会生成。

### 4. 当前为什么仍然保留 full.mp4

当前系统的这些后处理都依赖 `full.mp4`：

- 同步 viewer
- 时间线 CSV
- 视频与数据对齐
- 本地统一回看

所以现阶段不建议取消 `full.mp4`。更合理的做法是：

- 把 `full.mp4` 当主档
- 把 `analysis_5m.mp4` 当可选副本

### 5. 左右并排分析成片

如果你需要把“左侧比赛视频 + 右侧数据面板”直接导出成一条成品分析视频，而不是只在浏览器里看同步页，可以使用：

- 脚本：`compose_sync_viewer_video.py`
- 输出文件名：`__analysis_side_by_side.mp4`

它的做法是：

- 打开本地 `__sync_viewer.html`
- 自动开始播放
- 录制浏览器内容区域
- 导出一条可直接回看、剪辑、二次分析的左右并排成片

单场示例：

```bash
python3 /Users/niannianshunjing/match_plan/recordings/compose_sync_viewer_video.py \
  "/Volumes/990 PRO PCIe 4T/match_plan_recordings/2026-03-25/session_20260325_101907_278695/FT_Al-Arabi_SC_vs_Al_Waab_20260325_101907_278695/FT_Al-Arabi_SC_vs_Al_Waab__20260325_101907_278695__sync_viewer.html" \
  --browser safari
```

也可以直接对整个 session 目录批量导出：

```bash
python3 /Users/niannianshunjing/match_plan/recordings/compose_sync_viewer_video.py \
  "/Volumes/990 PRO PCIe 4T/match_plan_recordings/2026-03-25/session_20260325_101907_278695"
```

当前默认使用 Safari 导出，因为这条链对本地 `sync_viewer` 页面和视频自动起播更稳定。

## 压缩测试结论

这里记录一组真实样本测试，方便后续统一口径。

测试样本：

- 单场足球
- 时长约 `30` 分钟
- 分辨率 `1088x680`
- 原始 `full.mp4` 约 `1.90 GB`

测试结果：

### HEVC 3 Mbps

- 压缩后约 `0.63 GB`
- 体积下降约 `66.8%`
- 肉眼观感还能接受，但更偏“空间优先”
- 不建议作为长期唯一分析母版

### HEVC 5 Mbps

- 压缩后约 `0.94 GB`
- 体积下降约 `50%`
- 对比分牌、小球员、远景等细节更稳
- 更适合作为“analysis 副本”

### 推荐结论

- 主档：保留原始 `full.mp4`
- 可选副本：生成 `analysis_5m.mp4`
- 不建议默认只保留 `3 Mbps` 压缩版

## 架构

```
┌─────────────────────────────────────────────────────┐
│                  run_auto_capture.py                 │
│                                                     │
│  ┌──────────┐  ┌──────────┐  ┌──────────────────┐  │
│  │ Ao vivo  │→│ 用户选择  │→│ 打开视频窗口     │  │
│  │ 发现直播 │  │ 运动/比赛│  │ AppleScript/pinch│  │
│  └──────────┘  └──────────┘  └───────┬──────────┘  │
│                                       ↓              │
│  ┌──────────────────────────────────────────────┐   │
│  │  PyObjC 检测窗口 → ffmpeg 多路录制           │   │
│  │         + BettingDataPoller 秒级数据采集       │   │
│  └──────────────────────────────────────────────┘   │
│                                       ↓              │
│  ┌──────────┐  ┌──────────┐  ┌──────────────────┐  │
│  │ 合并视频 │→│ 数据匹配  │→│ 输出 session 目录│  │
│  └──────────┘  └──────────┘  └──────────────────┘  │
└─────────────────────────────────────────────────────┘

数据源:
  sftraders.live/schedules/live ──→ AppleScript/pinchtab ──→ 浏览器 watch 窗口
  Chrome CDP / dashboard / API  ──→ BettingDataPoller      ──→ betting_data.jsonl
  浏览器 watch 窗口             ──→ PyObjC 窗口检测        ──→ 窗口录制
```

## 环境要求

- **操作系统**: macOS (Apple Silicon 或 Intel)
- **Python**: 3.10+
- **ffmpeg**: `brew install ffmpeg`
- **浏览器**: 支持 `Google Chrome` 或 `Safari`，并且当前有一个 `schedules/live` 标签页
- **PyObjC**: `pip install pyobjc-core pyobjc-framework-Quartz`
- **psutil**: `pip install psutil`
- **pinchtab**: 仅 Chrome 可选备用方式，脚本会自动在 `PATH`、Homebrew 和 NVM 常见路径里查找
- **Chrome CDP**: 如需直接复用浏览器登录态抓投注数据，需要为 Chrome 开启远程调试

## 启动前基础条件

这是当前版本最重要的一节。只要这几项没满足，脚本就会误判或启动失败。

### 1. 浏览器侧条件

- 必须使用真实 `Google Chrome` 或 `Safari`
- 必须已经手动登录 `sftraders.live`
- 必须已经打开 `schedules/live` 页面
- 当前页面里必须能看到 `Ao vivo` 页签下的直播比赛
- Chrome 模式下最好开启 `View > Developer > Allow JavaScript from Apple Events`
- Safari 模式下最好开启 `开发 > 允许来自 Apple Events 的 JavaScript`

说明:

- 只需要这一套前端登录态，不需要在浏览器里再登录第二个站点
- 脚本会显式检查当前是不是登录页、是不是 `schedules` 页
- 如果你停留在别的页面，脚本会直接报错退出

### 2. 数据侧条件

当前版本的数据链优先级如下:

1. 浏览器会话数据源
2. 本地 dashboard
3. `live_dashboard.env` 凭据直连 API

#### 2.1 浏览器会话数据源（优先）

如果 Chrome 开启了 CDP，脚本会优先从当前已登录浏览器里捕获真实投注请求，然后直接复用那条请求链持续轮询数据。

满足条件:

- Chrome 已登录 `sftraders.live`
- Chrome 已开启远程调试
- `CHROME_CDP_URL` 可访问，或者默认的 `http://127.0.0.1:9222`

当前实现会从 `schedules/live` 或 `watch` 页的网络请求里自动提取:

- `transform.php` 的真实 URL
- 当前浏览器会话的 `Cookie`
- 请求体模板

然后把这些信息注入 `BettingDataPoller`，不再强依赖 `.env` 里的账号密码。

#### 2.2 dashboard / env 备用链路

如果浏览器会话数据源不可用，则满足下面任一项即可:

- 本地 dashboard 已经启动，并且 `http://127.0.0.1:8765/api/latest.json` 可访问
- 或者 `live_dashboard.env` 里配置了有效的登录凭据，脚本/服务可以自行登录

## 安装

```bash
cd ~/Desktop/recordings
pip install -r requirements.txt
```

## 配置

赔率 API 凭据配置在项目根目录的 `live_dashboard.env`：

```env
LOGIN_USERNAME=xxx
LOGIN_PASSWORD=xxx
ENTRY_URL=https://hga035.com
```

说明:

- `ENTRY_URL` 是数据侧登录入口，不是前端视频页
- 它属于 API / dashboard 登录链，不要求你在浏览器里手动打开
- 当浏览器会话数据源可用时，`.env` 会退为备用方案

### 3. macOS 权限条件

给“实际运行脚本的宿主应用”授权，不是给 `.py` 文件授权。

- `系统设置 > 隐私与安全性 > 屏幕与系统音频录制`
- `系统设置 > 隐私与安全性 > 自动化`
- `系统设置 > 隐私与安全性 > 辅助功能`

通常要授权的宿主应用是:

- 你在 `Codex.app` 里跑，就给 `Codex`
- 你在 `Terminal` 里跑，就给 `Terminal`
- 你在 `iTerm` 里跑，就给 `iTerm`

如果少了这些权限，脚本常见表现是:

- `ffmpeg 启动失败！检查屏幕录制权限`
- AppleScript 不能控制浏览器
- 无法发送 `Esc` 清理翻译弹窗

### 4. 显示器条件

- 单路录制可以只用一个屏幕
- 多路录制强烈建议副屏
- 脚本会把多个 `watch` 窗口按横屏比例缩放排布，尽量避免黑边和相互遮挡

## 推荐启动顺序

每次正式跑之前，按这个顺序来:

1. 运行预检脚本，自动安装缺失依赖
2. 在真实浏览器手动登录 `sftraders.live`
3. 手动进入 `schedules/live`
4. 如果你想直接复用浏览器登录态抓数据，并且你用的是 Chrome，先开启 Chrome CDP
5. 确认 `Ao vivo` 页签下确实有正在直播的比赛
6. 再运行 `run_auto_capture.py`

示例:

```bash
cd /Users/lutiancheng/lifeSpaces/recordings

# 1. 先做环境预检并自动安装缺失项
python3 preflight_setup.py --auto-install

# 2. 如果你不走浏览器会话数据源，再启动本地数据服务
./start_dashboard.sh

# 3. 确认浏览器已手动登录并停在 schedules/live

# 4. 启动录制
python3 run_auto_capture.py --all --max-streams 4
```

Safari 示例:

```bash
python3 run_auto_capture.py --browser safari --all --max-streams 1 --segment-minutes 1 --max-duration-minutes 1
```

Chrome 示例:

```bash
python3 run_auto_capture.py --browser chrome --all --max-streams 1 --segment-minutes 1 --max-duration-minutes 1
```

生成可选分析副本：

```bash
python3 run_auto_capture.py --all --analysis-5m
```

指定 analysis 副本码率：

```bash
python3 run_auto_capture.py --all --analysis-5m --analysis-mbps 5
```

也可以直接使用一键启动脚本，它会先自动跑预检:

```bash
./start_recording.sh FT 2 10
```

### 开启 Chrome CDP

浏览器会话数据源依赖 Chrome 远程调试。当前脚本会自动探测:

- `CHROME_CDP_URL`
- `http://127.0.0.1:9222`
- `http://127.0.0.1:9223`
- `http://127.0.0.1:9333`

如果这些端点都不可用，脚本会自动回退到 dashboard / `.env`。

建议做法:

- 启动一个带远程调试端口的 Chrome 会话
- 或者把当前可用的 CDP 地址写到环境变量 `CHROME_CDP_URL`

## 使用方法

### 基本用法

```bash
# 交互模式：优先读取 schedules/live 的 Ao vivo 直播，让用户选择
python3 run_auto_capture.py

# 指定运动类型（跳过交互选择）
python3 run_auto_capture.py --gtypes FT

# 录制所有比赛
python3 run_auto_capture.py --all

# 用 Safari 作为前端浏览器
python3 run_auto_capture.py --browser safari --all

# 限制最多4路
python3 run_auto_capture.py --max-streams 4

# 每5分钟分段
python3 run_auto_capture.py --segment-minutes 5

# 无录制时长限制
python3 run_auto_capture.py --max-duration-minutes 0
```

### CLI 参数

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `--max-streams` | 8 | 最大同时录制路数 |
| `--mode` | auto | 打开视频方式: auto/applescript/pinchtab |
| `--browser` | chrome | 浏览器类型: chrome/safari |
| `--gtypes` | (交互) | 运动类型: FT,BK,ES,TN,VB,BM,TT,BS,SK,OP |
| `--all` | false | 录制所有比赛（跳过选择） |
| `--segment-minutes` | 10 | 分段保存间隔（分钟） |
| `--max-duration-minutes` | 180 | 单场上限时长（0=无限） |

### 当前选择逻辑

- 优先从真实浏览器的 `schedules/live > Ao vivo` 页签读取可观看直播
- 每一行抓取 `league / home / away / watch_url`
- 赛程时间只作为弱参考；只要前端已有可点击的 `watch_url`，就优先尝试打开并验证是否真正开始播放
- 不会再单纯因为“距离开赛还有 X 分钟”就硬跳过已经可点击的直播页
- watch 任务后续补位时，Safari 新开的 `watch` 窗口会避让当前已存在的直播窗口，不再从左上角重复覆盖
- 单个 session 收尾时只关闭本 session 对应的 `watch` 页面，不会再误关其他并发录制中的直播窗口
- 用户选择后，按 `league + home + away` 三元匹配到对应 `watch` 页面
- 匹配不上会直接报错，不再退化成“随便打开前几个链接”
- 数据采集线程会优先尝试浏览器会话数据源，失败后回退到 dashboard / `.env`
- 最终录像和投注数据会一起落到同一个 `session` 目录

### 交互选择流程

```
发现 23 场直播比赛：

  [FT] 足球 (15场)
    1. 巴萨 vs 皇马          (西甲)     2-1
    2. 利物浦 vs 曼城        (英超)     0-0
    ...

  [BK] 篮球 (5场)
    16. 湖人 vs 凯尔特人      (NBA)     98-102
    ...

请选择要录制的比赛 (最多 8 路):
  - 运动代码: FT, BK, TT ...
  - 序号: 1,3,5
  - all: 全部
  - q: 退出

你的选择: FT
```

## 输出文件说明

每次录制创建一个独立的 session 目录：

```
sessions/session_20260324_120000/
├── recording.log                    # 完整录制日志（时间戳+事件）
├── session_result.json              # 汇总报告（JSON）
├── raw_betting_data.jsonl           # 所有比赛的原始秒级数据
├── aligned_events.jsonl             # 比分变化事件 + 视频位置
└── stream_1_Barca_vs_RealMadrid/
    ├── manifest.json                # 录制时间线（分段信息、卡顿记录）
    ├── seg_001.mp4                  # 第1段视频 (0-10分钟)
    ├── seg_002.mp4                  # 第2段视频 (10-20分钟)
    ├── gap_001.mp4                  # 卡顿黑帧填充（如有）
    ├── Barca_vs_RealMadrid_full.mp4 # 合并后完整视频
    ├── betting_data.jsonl           # 该比赛全部秒级赔率数据
    └── betting_data_001.jsonl       # 第1段数据备份
```

### 关键文件格式

**betting_data.jsonl** — 每秒一条，JSONL 格式：
```json
{"timestamp":"2026-03-24T06:00:01+00:00","gtype":"FT","gid":"12345","team_h":"巴萨","team_c":"皇马","score_h":"2","score_c":"1","fields":{"IOR_RMH":"1.85","IOR_RMC":"4.50","IOR_REH":"0.92",...}}
```

**session_result.json** — 汇总报告：
```json
{
  "session_id": "20260324_120000",
  "recording": {"start": "...", "end": "...", "actual_duration_sec": 1800.5, "streams": 4},
  "data": {"total_records": 7200, "poll_count": 1800, "error_count": 2},
  "streams": [{"index":1, "match_id":"...", "merged_video":"...", "segments":3, "freeze_count":1}],
  "events": [{"event_type":"score_change","team_h":"巴萨","prev_score":"1-1","new_score":"2-1"}],
  "aligned": [...]
}
```

**aligned_events.jsonl** — 比分变化事件：
```json
{"event_type":"score_change","timestamp":"...","team_h":"巴萨","team_c":"皇马","prev_score":"1-1","new_score":"2-1","stream_idx":0}
```

### betting_data.jsonl 中 fields 字段说明

每条记录的 `fields` 包含完整赔率数据：

| 字段 | 说明 |
|------|------|
| `SCORE_H` / `SCORE_C` | 主/客队比分 |
| `IOR_RMH` / `IOR_RMN` / `IOR_RMC` | 独赢（主胜/平/客胜）赔率 |
| `RATIO_RE` / `IOR_REH` / `IOR_REC` | 让球（盘口/主/客） |
| `RATIO_ROUO` / `IOR_ROUH` / `IOR_ROUC` | 大小盘（盘口/大/小） |
| `RUNNING` | 是否滚球 (Y/N) |
| `NOW_MODEL` | 比赛时间（如 "2nd Half 65'"） |
| `STRONG` | 让球方 (H/C) |

## 模块说明

| 文件 | 职责 |
|------|------|
| `run_auto_capture.py` | 全自动主脚本（新建） |
| `recorder.py` | ffmpeg 多路录制核心，卡顿检测，黑帧填充 |
| `post_match.py` | 视频分段合并，帧提取 |
| `aligner.py` | 视频时间轴与数据时间轴对齐 |
| `poll_get_game_list.py` | 比赛列表与赔率 XML 拉取、解析与轮询 |
| `auto_login.py` | 自动登录并生成 cookie / body 模板 |
| `serve_live_dashboard.py` | 本地 live dashboard 服务，输出 `latest.json` |
| `db_store.py` | live dashboard 的 SQLite 存储层 |
| `start_dashboard.sh` | 一键启动本地 dashboard 服务 |
| `start_recording.sh` | 一键启动录制脚本 |

## 故障处理

### 自动恢复机制

- **卡顿**: 文件大小 20 秒不增长 → 插入黑帧 + 重启录制
- **黑屏**: 帧亮度过低 → 自动刷新 Chrome 页面
- **网络错误**: API 请求自动重试 2 次
- **比赛结束**: 自动停止该路录制，合并视频，通知用户

### 日志

每次录制生成独立的 `recording.log`，记录完整过程：
- 录制开始/结束时间
- 每路窗口检测坐标
- 分段保存时间和数据量
- 卡顿/黑屏检测和恢复
- 比赛结束通知
- 错误信息

## 运动类型代码

| 代码 | 运动 |
|------|------|
| FT | 足球 |
| BK | 篮球 |
| ES | 电竞 |
| TN | 网球 |
| VB | 排球 |
| BM | 羽毛球 |
| TT | 乒乓球 |
| BS | 棒球 |
| SK | 斯诺克 |
| OP | 其他 |

---

## 数据源服务（live_dashboard）

`live_dashboard` 现在的定位是：

- 本地实时数据面板
- 本地 `latest.json` 快照缓存
- 录制/watch 的 fallback 数据源

录制脚本与 watch 调度当前默认顺序是：

1. 优先使用 `live_dashboard.env` 直连 API
2. 如果直连失败或本轮快照不可用，再 fallback 到本地 dashboard:
   - `http://127.0.0.1:8765/api/latest.json`

另外，watch 任务会自动同步本地 dashboard 的球种范围：

- 当前 watch 任务全量不筛选时，dashboard 会自动切到全 sport
- 当前 watch 任务只筛某些球种时，dashboard 会自动切到对应 `GTYPES`
- 因此 `latest.json` 不再固定只抓 `FT`

### 启动数据源服务

```bash
cd recordings

# 1. 配置凭据
cp live_dashboard.env.example live_dashboard.env
# 编辑 live_dashboard.env，填入用户名密码

# 2. 启动数据采集+服务
python3 serve_live_dashboard.py

# 服务地址: http://127.0.0.1:8765
# API端点:
#   GET /api/latest.json    # 最新采集数据（本地快照 / fallback）
#   GET /status.json        # 服务状态
```

### live_dashboard 工作原理

```
serve_live_dashboard.py
├── poll_get_game_list.py   # 轮询赔率 API
├── auto_login.py           # 自动登录获取 cookie
├── db_store.py             # SQLite 存储历史数据
└── live_service_data/
    ├── history.db          # SQLite 数据库
    └── latest.json         # 最新结构化快照（不是登录态）
```

### 文件说明

| 文件 | 作用 |
|------|------|
| `serve_live_dashboard.py` | Web 服务（端口 8765），同时做数据采集 |
| `poll_get_game_list.py` | 轮询赔率 API，返回解析后的数据 |
| `auto_login.py` | 自动登录，获取/刷新 cookie |
| `db_store.py` | SQLite 数据库读写模块 |
| `live_dashboard.env` | 凭据配置（username/password/entry_url） |
| `live_service_data/latest.json` | 最新结构化快照（自动生成，不是登录态） |
