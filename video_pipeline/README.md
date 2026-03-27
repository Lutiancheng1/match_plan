# Video Pipeline

## 项目位置

- 项目根目录: [video_pipeline](/Users/niannianshunjing/match_plan/video_pipeline)
- 主脚本: [scripts/run_v6.py](/Users/niannianshunjing/match_plan/video_pipeline/scripts/run_v6.py)
- 任务配置: [tasks/tasks_v6.json](/Users/niannianshunjing/match_plan/video_pipeline/tasks/tasks_v6.json)

## 这套项目现在做什么

当前正式流程不是“6 个不同网站”，而是：

1. 复用 `sftraders.live` 的登录态
2. 打开赛程页 `https://sftraders.live/schedules`
3. 发现可录制比赛
4. 展开成最多 6 个并发录制任务
5. 输出截图、视频、结构化数据、报告和批次汇总

## 当前目录结构

```text
video_pipeline/
├── README.md
├── auth/
├── browser_profile/
├── data/
├── output/
├── runs/
├── scripts/
│   └── run_v6.py
├── tasks/
│   └── tasks_v6.json
└── videos/
```

## 启动命令

```bash
cd /Users/niannianshunjing/match_plan/video_pipeline
/opt/homebrew/bin/python3 -u /Users/niannianshunjing/match_plan/video_pipeline/scripts/run_v6.py \
  /Users/niannianshunjing/match_plan/video_pipeline/tasks/tasks_v6.json \
  --output-dir /Users/niannianshunjing/match_plan/video_pipeline/data \
  --max-workers 6
```

如果不传 `--output-dir`，主脚本现在默认也会写到项目自己的 `data/`。

常用启动入口：

- [start_video_pipeline.sh](/Users/niannianshunjing/match_plan/video_pipeline/start_video_pipeline.sh)
  默认走 `PinchTab + attach Chrome` 常驻赛程监听
- [start_video_pipeline_storage_state.sh](/Users/niannianshunjing/match_plan/video_pipeline/start_video_pipeline_storage_state.sh)
  强制走 `Playwright + sftraders.json`，适合直接复用已有登录态录制

当前默认监控策略已经改成：

- `monitor.max_cycles=0`：不限制轮次
- `monitor.empty_cycles_before_stop=0`：空场时也不自动停止
- `manual_session_timeout_seconds=86400`：等待人工接管最长 24 小时
- 启动脚本通过 `caffeinate` 保持 Mac 不休眠
- `discovery_persistent_context=true`：赛程监听页面固定复用一个可见浏览器 profile，不反复新开新关
- `block_new_window_when_visible=true`：只要系统检测到已有可见的 `SF Traders` 窗口，脚本就绝不再启动新的站点窗口
- `skip_storage_validation_when_visible_window=true`：只要系统检测到已有可见的 `SF Traders` 窗口，就先跳过登录态 1/3、2/3 预校验

## 当前配置重点

配置在 [tasks/tasks_v6.json](/Users/niannianshunjing/match_plan/video_pipeline/tasks/tasks_v6.json)。

当前关键行为：

- `mode=site_rooms`
- `discovery_backend=pinchtab`
- 登录页：`https://sftraders.live/login`
- 赛程页：`https://sftraders.live/schedules`
- 最大并发：`6`
- 单场录制时长：`300` 秒
- 监控循环：开启
- 赛程轮询间隔：`30` 秒
- 连续空轮次停止阈值：`6`
- 发现比赛阶段：`discovery_headless=false`
- 实际录制阶段：`headless=true`
- 浏览器通道：`chrome`
- 当前代理：`http://127.0.0.1:1082`
- 赛程监听后端：`PinchTab + attach Chrome`

## PinchTab 控制模式

现在赛程监听优先走 PinchTab，不再每轮新开一个 Playwright 浏览器去看一眼再关掉。

工作方式：

1. 启动一个专用 Chrome，并打开远程调试端口
2. 在那个专用 Chrome 中手动登录 `sftraders.live`
3. 手动停在 `https://sftraders.live/schedules`
4. `run_v6.py` 通过 PinchTab 接管这个现成标签页做常驻刷新和候选比赛发现
5. 真正开始录制时，仍由 Playwright 负责每场比赛的截图和视频文件输出

如果 PinchTab 一时识别不到现成赛程页，当前配置会先检查当前屏幕上是否已经有可见的 `SF Traders` Chrome 窗口；只有确认没有时，才会尝试直接新开一个可见 `Chrome` 窗口到 `https://sftraders.live/schedules/live`。如果已经检测到可见窗口，则会直接触发安全保护并放弃自动新开。

专用启动脚本：

- [scripts/start_pinchtab_control_browser.sh](/Users/niannianshunjing/match_plan/video_pipeline/scripts/start_pinchtab_control_browser.sh)

启动方式：

```bash
cd /Users/niannianshunjing/match_plan/video_pipeline
chmod +x /Users/niannianshunjing/match_plan/video_pipeline/scripts/start_pinchtab_control_browser.sh
/Users/niannianshunjing/match_plan/video_pipeline/scripts/start_pinchtab_control_browser.sh
```

## 登录和安全规则

当前策略是不允许脚本在没有人工确认的情况下乱开登录页。

规则如下：

- 优先复用 `auth/sftraders.json`
- 如果存在受控 Chrome，会优先尝试从那个浏览器导出新的 `storage_state`
- 如果登录态失效，先等待人工接管
- 可以发 Telegram 通知
- 不自动反复尝试登录

原因：

- `sftraders.live` 有 Cloudflare / 真人验证
- 连续无效登录容易触发风控
- PinchTab 模式默认只复用已经打开的 `sftraders.live` 标签页，不会在识别不到目标页时自动乱导航

## 当前恢复状态

这次是从补丁副本重建出来的项目骨架。

已经恢复的内容：

- 主脚本 [scripts/run_v6.py](/Users/niannianshunjing/match_plan/video_pipeline/scripts/run_v6.py)
- 任务配置 [tasks/tasks_v6.json](/Users/niannianshunjing/match_plan/video_pipeline/tasks/tasks_v6.json)
- 运行目录结构
- Playwright 代理支持
- 相对项目根目录的默认输出路径

目前还需要人工确认或重新准备的内容：

- `browser_profile/sftraders`
- 历史录制数据和旧批次输出

当前已经恢复：

- [auth/sftraders.json](/Users/niannianshunjing/match_plan/video_pipeline/auth/sftraders.json)
- [start_video_pipeline.sh](/Users/niannianshunjing/match_plan/video_pipeline/start_video_pipeline.sh)
- [.env.example](/Users/niannianshunjing/match_plan/video_pipeline/.env.example)

## 已知事实

- 之前已经确认：正常浏览器登录后，赛程页可以返回 `200`
- 赛程页不一定直接给 `<a href="/watch">`
- 是否能录，取决于页面最右侧播流按钮是否真正可用
- 如果页面上只有灰色按钮，说明站点还没放出可录制视频源，不是脚本故障

## 给接手 AI 的最短阅读顺序

1. [README.md](/Users/niannianshunjing/match_plan/video_pipeline/README.md)
2. [scripts/run_v6.py](/Users/niannianshunjing/match_plan/video_pipeline/scripts/run_v6.py)
3. [tasks/tasks_v6.json](/Users/niannianshunjing/match_plan/video_pipeline/tasks/tasks_v6.json)

## 建议的下一步

1. 用当前 `auth/sftraders.json` 做一次冒烟验证
2. 如果登录态失效，再从已登录浏览器重新生成
3. 再恢复长期监控和录制
