# 代理与网络配置说明

## 概览

项目中有两层代理：

1. **sing-box** — 全局出口代理，把特定域名/IP 的流量转发到香港节点
2. **data_site_proxy.py** — 本地 HTTP 反向代理，解决 WKWebView 的 SSL 证书问题

```
浏览器/WKWebView
    |
    v
data_site_proxy.py (localhost:18780, HTTP)
    |  设置 http_proxy/https_proxy 环境变量
    v
sing-box (localhost:17897, mixed HTTP/SOCKS5)
    |  根据路由规则选择出口
    v
香港节点 (hysteria2/vmess) ──> 目标站点
```

---

## sing-box

### 文件位置

| 文件 | 说明 |
|---|---|
| `recordings/.bin/sing-box` | sing-box 可执行文件 (v1.13.4, darwin-arm64) |
| `recordings/watch_runtime/proxy_runtime/recording_singbox.json` | 主配置文件 |
| `recordings/watch_runtime/proxy_runtime/recording_singbox.meta.json` | 节点元数据 |
| `recordings/watch_runtime/proxy_runtime/recording_singbox.state.json` | 运行状态 |
| `recordings/watch_runtime/proxy_runtime/sing-box.log` | 运行日志（会持续增长，定期清空） |

### 启动/重启

```bash
# 启动
nohup recordings/.bin/sing-box run -c recordings/watch_runtime/proxy_runtime/recording_singbox.json > /tmp/singbox.log 2>&1 &

# 重启（修改配置后）
pkill -f "sing-box run" && sleep 2
nohup recordings/.bin/sing-box run -c recordings/watch_runtime/proxy_runtime/recording_singbox.json > /tmp/singbox.log 2>&1 &

# 验证
curl -x http://127.0.0.1:17897 https://cp.cloudflare.com/generate_204 -v
```

### 监听

- 地址: `127.0.0.1:17897`
- 类型: mixed (HTTP + SOCKS5)
- `set_system_proxy: true` — 会自动设置系统代理

### 路由规则

| 域名/IP | 出口池 | 用途 |
|---|---|---|
| `112.121.42.168` (IP) | recording_data_pool | 数据站 IP 直连 |
| `hga035.com` | recording_data_pool | 数据站域名 |
| `hga038.com` | recording_data_pool | 数据站备用域名 |
| `mos011.com` | recording_data_pool | 数据站 SSL 证书域名 |
| `niab12345.com` | recording_data_pool | 数据站 CU 检测子域名 (scu.niab12345.com 等) |
| `sftraders.live` | recording_live_pool | 视频站 |
| 其他所有流量 | direct | 直连 |

### 出口池

- **recording_data_pool**: 数据站专用，15 个节点，urltest 自动选最快
- **recording_live_pool**: 视频站专用，26 个节点，urltest 自动选最快
- 节点类型: hysteria2 + vmess 混合

### 新增域名走代理

编辑 `recording_singbox.json` 的 `route.rules`，在对应的 `domain_suffix` 数组中添加域名，然后重启 sing-box。

---

## data_site_proxy.py（数据站本地反向代理）

### 文件位置

`recordings/mac_app/MatchPlanRecorderApp/data_site_proxy.py`

### 为什么需要它

数据站 `112.121.42.168` 使用的 SSL 证书签发给 `*.mos011.com`，IP 直连时 WKWebView 会拒绝证书。
本地反向代理以 HTTP 方式暴露给 WKWebView，内部用 `urllib` 连上游时跳过 SSL 验证。

### 功能

1. **自动登录**: 首次请求 `/` 时，调用 `auto_login` 获取凭证，拼接登录参数重定向（只做一次，防止循环）
2. **URL 改写**: 把响应中的 `https://112.121.42.168` 替换为 `http://127.0.0.1:18780`
3. **Cookie 注入**: 把登录 cookie 注入响应，domain 改写为 localhost
4. **JS 修复**: `getWebDomain()` 从 `dom.domain`（无端口）改为 `dom.location.host`（含端口）

### 监听

- 地址: `http://127.0.0.1:18780`
- 上游: `https://112.121.42.168`（通过 sing-box 代理出去）

### 端点

| 路径 | 说明 |
|---|---|
| `/` | 首次自动登录重定向，后续直接透传 |
| `/ping` | 健康检查，返回 `ok`（不触发 auto_login） |
| `/_errors` | 返回收集到的 JS 错误（调试用） |
| `/_report_error` | JS 上报错误端点（POST） |
| 其他 | 透传到上游 |

---

## 凭证配置

### 文件位置（优先级从高到低）

1. 环境变量: `LOGIN_USERNAME`, `LOGIN_PASSWORD`
2. `recordings/live_dashboard.env`
3. `live_dashboard/live_dashboard.env`

### 格式

```
LOGIN_USERNAME=ww88188
LOGIN_PASSWORD=Aa1122331
```

### auto_login 模块

位于 `recordings/auto_login.py` 和 `live_dashboard/auto_login.py`（同一份代码的两个副本）。
返回 `{"cookie": "...", "uid": "...", "mid": "..."}`。

---

## 小火箭 (Shadowrocket) 配置

备份在 `docs/shadowrocket_config.conf`，这是 iOS 端的代理规则，与 sing-box 独立。

---

## 常见问题

### sing-box 日志太大
```bash
> recordings/watch_runtime/proxy_runtime/sing-box.log
```

### 数据站页面白屏/无限重定向
1. 确认 sing-box 在跑: `curl -x http://127.0.0.1:17897 https://cp.cloudflare.com/generate_204`
2. 确认 data_site_proxy 在跑: `curl http://127.0.0.1:18780/ping`
3. 检查账户是否被封: `transform.php?p=get_game_list` 返回 `CheckEMNU` 说明账户受限

### 新增域名需要走代理
编辑 `recording_singbox.json` → `route.rules` → 对应 `domain_suffix` 数组 → 重启 sing-box
