# Match Plan

足球实时事件驱动盘口套利研究平台。

路线：**先懂球 → 再规则 → 再盘口 → 再交易**

## 项目结构

```
match_plan/
├── recordings/                 # 录制系统（当前主力）
│   ├── mac_app/                #   macOS 控制台 App
│   ├── pion_gst_direct_chain/  #   Pion+GStreamer 录制后端
│   ├── auto_login.py           #   数据站自动登录
│   ├── poll_get_game_list.py   #   盘口数据轮询
│   ├── .bin/                   #   sing-box 等二进制工具
│   └── watch_runtime/          #   运行时状态和代理配置
├── live_dashboard/             # 本地实时比赛看板
├── analysis_vlm/               # VLM 观察模型训练与评测
│   ├── benchmarks/             #   benchmark 脚本
│   ├── datasets/               #   数据集定义
│   ├── lib/                    #   运行时库 (LiveObserver 等)
│   ├── schemas/                #   模型输出 JSON schema
│   ├── tools/                  #   样本构建工具
│   └── training/               #   LoRA 微调实验
├── docs/                       # 文档
│   ├── plans/                  #   设计文档和追踪器
│   ├── proxy-and-network-setup.md  # 代理与网络配置
│   └── shadowrocket_config.conf    # 小火箭备份
└── README.md
```

## 当前主线

### 1. 录制

- **App** (`recordings/mac_app/MatchPlanRecorderApp`) — macOS 原生控制台
- **后端** (`recordings/pion_gst_direct_chain`) — Pion+GStreamer 直连 LiveKit 录制
- 产物存储：`/Volumes/990 PRO PCIe 4T/match_plan_recordings/`

### 2. 数据

- **live_dashboard** — 本地看板，持续轮询源站盘口数据
- **盘口绑定** — 录制时自动绑定盘口数据到视频

### 3. 分析

- **analysis_vlm** — 训练足球观察模型
- 当前生产模型：Qwen3.5-VL-9B-8bit (oMLX)，不需要 LoRA
- 数据集库：`/Volumes/990 PRO PCIe 4T/match_plan_dataset_library/`

## 网络与代理

详见 [docs/proxy-and-network-setup.md](docs/proxy-and-network-setup.md)

- **sing-box** (localhost:17897) — 数据站/视频站流量走香港节点
- **data_site_proxy** (localhost:18780) — 数据站本地反向代理，解决 SSL 证书问题

## 关键凭证

`recordings/live_dashboard.env` 或 `live_dashboard/live_dashboard.env`：

```
LOGIN_USERNAME=<数据站用户名>
LOGIN_PASSWORD=<数据站密码>
```

## 阅读顺序

1. 本文件
2. [recordings/README.md](recordings/README.md) — 录制系统总览
3. [recordings/pion_gst_direct_chain/README.md](recordings/pion_gst_direct_chain/README.md) — 录制后端
4. [recordings/mac_app/MatchPlanRecorderApp/README.md](recordings/mac_app/MatchPlanRecorderApp/README.md) — App
5. [live_dashboard/README.md](live_dashboard/README.md) — 看板
6. [analysis_vlm/README.md](analysis_vlm/README.md) — 分析
7. [docs/proxy-and-network-setup.md](docs/proxy-and-network-setup.md) — 代理配置
