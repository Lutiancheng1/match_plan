commit cb230177b8541a78c3fc4a43020a2aa1f61d4287
Author: Mayun1988998 <yaoxinghe820@gmail.com>
Date:   Mon Mar 30 02:57:03 2026 -0700

    Stabilize app-based recorder pipeline and docs

diff --git a/recordings/mac_app/MatchPlanRecorderApp/README.md b/recordings/mac_app/MatchPlanRecorderApp/README.md
new file mode 100644
index 0000000..1306766
--- /dev/null
+++ b/recordings/mac_app/MatchPlanRecorderApp/README.md
@@ -0,0 +1,123 @@
+# MatchPlan Recorder App
+
+这是当前正式使用的 macOS 原生控制台。
+
+## App 负责什么
+
+- 内嵌登录 `sftraders.live`
+- 维护 App 自己的会话，不打扰外部浏览器
+- 配置 formal / test 录制参数
+- 启动、停止、确保运行、重启
+- 展示当前活跃 Worker、历史、产物、日志
+- 管理删除历史和产物
+
+## 不负责什么
+
+- App 本身不直接接流
+- 实际接流和写盘仍由后端：
+  - `/Users/niannianshunjing/match_plan/recordings/pion_gst_direct_chain`
+
+## 当前页签说明
+
+### 总览
+
+- 当前运行阶段
+- 活跃 worker 数
+- 当前录制数
+- 总录制时长
+
+### 登录页
+
+- App 内嵌的 `sftraders.live/schedules/live`
+- 后端默认直接使用这张页的会话
+
+### 数据站
+
+- 内嵌 `hga035.com`
+- 用于后续直接查看源站数据页
+
+### Worker
+
+- 只显示当前活跃 worker
+- 显示阶段、段数、HLS 数、近 8 秒 fps、最后收包时间
+
+### 历史
+
+- 只显示已结束：
+  - `completed`
+  - `failed`
+  - `stopped`
+  - `skipped`
+
+### 产物
+
+- 显示当前 session 产物
+- 支持多选删除
+- 对正在录制的条目默认禁直接删
+- 可选“停止后删除”
+
+### 日志
+
+- 同时看：
+  - App 自己的操作日志
+  - 后台 dispatcher / worker 日志
+
+## 当前配置项
+
+- 运行模式：
+  - `formalBoundOnly`
+  - `bestEffortAll`
+- 比赛分类：`FT/BK/...`
+- 发现间隔
+- 循环频率
+- 分段时长
+- 最大并发
+- 画质与码率
+- 飞书通知开关
+
+## 当前状态提示规则
+
+- `等待登录`
+  - App bridge 还没准备好
+- `监听中`
+  - dispatcher 活着，但当前没有录制
+- `录制中`
+  - 当前已有 worker 真正在录
+
+## 会话与 bridge
+
+- App 自己持有 `WKWebView` 会话
+- 后端默认走：
+  - `MATCH_PLAN_APP_WEB_BRIDGE_URL`
+  - `MATCH_PLAN_APP_WEB_BRIDGE_FALLBACK_TO_BROWSER=0`
+
+所以正常情况下：
+- 不依赖外部 Safari
+- 不会抢你外面的浏览器操作
+
+## 构建与打包
+
+### 本地运行
+
+```bash
+cd /Users/niannianshunjing/match_plan/recordings/mac_app/MatchPlanRecorderApp
+swift build
+swift run
+```
+
+### 打包
+
+```bash
+cd /Users/niannianshunjing/match_plan/recordings/mac_app/MatchPlanRecorderApp
+./build_app_bundle.sh
+```
+
+当前输出：
+
+- `/Users/niannianshunjing/match_plan/recordings/mac_app/MatchPlanRecorderApp/dist/MatchPlanRecorderApp.app`
+
+## 当前结论
+
+- App 已经是当前主入口
+- 外部浏览器已不再是默认依赖
+- 正式录制、历史查看、产物管理、日志查看都已经在 App 里闭环
