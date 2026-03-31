# MatchPlan Recorder App

这是当前正式使用的 macOS 原生控制台。

## App 负责什么

- 内嵌登录 `sftraders.live`
- 内嵌数据站页面（通过 `data_site_proxy.py` 代理）
- 配置 formal / test 录制参数
- 启动、停止、确保运行、重启
- 展示当前活跃 Worker、历史、产物、日志
- 管理删除历史和产物

## 不负责什么

- App 本身不直接接流
- 实际接流和写盘由后端 `pion_gst_direct_chain/` 完成

## 数据站代理 (data_site_proxy.py)

App 内置一个本地反向代理，运行在 `127.0.0.1:18780`：

### 核心功能

1. **反向代理**: 转发数据站请求到上游 `https://112.121.42.168`，绕过 SSL 证书问题
2. **单一 session 管理**: 唯一调用 `auto_login()` 的组件，避免 doubleLogin
3. **session 共享端点**:
   - `GET /credentials` — 返回当前缓存的 cookie/mid/uid/body_template
   - `GET /credentials/refresh` — 强制重新登录并返回新 session
4. **页面改写**: 修正 `needsTrans`、IP 直连地址、域名等，使页面在本地代理下正常工作
5. **启动时 pre-login**: 服务器启动前就准备好 session

### 为什么 proxy 是 session 唯一出口

数据站强制单 session — 同一账号新登录会踢旧 session (doubleLogin)。

之前的问题：
- proxy 登录一次 → App 数据站页面能用
- dispatcher 又登录一次 → proxy 的 session 失效
- worker 又各自登录 → dispatcher 的 session 也失效

现在的方案：
- **只有 proxy 调用 `auto_login()`**
- dispatcher 从 `GET /credentials` 获取 session
- worker 从 dispatcher 的共享凭证文件获取 session
- 全链路共享同一个 session，不会互相踢

## 当前页签说明

### 总览
- 当前运行阶段、活跃 worker 数、录制数、总时长

### 登录页
- App 内嵌的 `sftraders.live/schedules/live`
- 后端通过 bridge 使用这张页的 live 列表

### 数据站
- 内嵌数据站页面 (通过 proxy 代理)
- 用于查看源站数据

### Worker
- 只显示当前活跃 worker
- 显示阶段、段数、HLS 数、fps、最后收包时间

### 历史
- 已结束的 worker: completed / failed / stopped / skipped

### 产物
- 当前 session 产物、支持多选删除
- 对正在录制的条目默认禁直接删

### 日志
- App 操作日志 + dispatcher / worker 日志

## 当前配置项

- 运行模式: `formalBoundOnly` / `bestEffortAll`
- 比赛分类: `FT/BK/...`
- 发现间隔、循环频率、分段时长
- 最大并发、画质与码率
- 飞书通知开关

## 当前状态提示规则

- `等待登录` — App bridge 还没准备好
- `监听中` — dispatcher 活着，当前没有录制
- `录制中` — 有 worker 在录

## 构建与打包

### 本地运行

```bash
cd /Users/niannianshunjing/match_plan/recordings/mac_app/MatchPlanRecorderApp
swift build
swift run
```

### 打包

```bash
cd /Users/niannianshunjing/match_plan/recordings/mac_app/MatchPlanRecorderApp
./build_app_bundle.sh
```

输出: `dist/MatchPlanRecorderApp.app`
