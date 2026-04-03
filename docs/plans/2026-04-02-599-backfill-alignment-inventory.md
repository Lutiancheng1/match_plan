# 599 Live Text 回补 & 视频对齐清单

> 日期：2026-04-02  
> 目的：梳理所有录像 × 599数据的对齐状态，为后续 VLM 训练/微调提供数据底账

---

## 一、数据总览

| 数据源 | 录像数 | 有599数据 | 视频+599已对齐 | 可用于训练 |
|---|---|---|---|---|
| **03-24~03-31 全场录像（回补+对齐）** | 274 | **137** | **137场** ✅ | ✅ **主力数据** |
| pgstapp 04-01（新pipeline自带599） | 22 | 22 | **18场** (>=30min+aligned) | ✅ 最佳 |
| Desktop sessions（03-24 老pipeline回补） | 13 | 13 | 8场（1-5min片段） | ⚠️ 片段太短 |
| 外置盘 _video_without_local_data（回补） | 32 | 32 | **0场**（视频在赛前，不含比赛） | ❌ 仅599文本 |
| Gold matches 库 | 58 | 0 | 0 | ⚠️ 有timeline+视频，可回补 |

**当前 Tier-1 可训练数据：155 场全场录像 + 141,938 条599事件（已对齐 `_video_pos_sec`）**

---

## 二、Tier-1：04-01 pgstapp 全场录像 + 599 对齐（18场）

这是最高质量的数据——完整比赛视频 + 599 live text 自动对齐，可直接用于 VLM 训练。

| # | 比赛 | 时长 | 599事件数 | 对齐状态 |
|---|---|---|---|---|
| 1 | Bayern Munich W vs Manchester United W | 49.8min | 894 | ✅ aligned |
| 2 | FC Andorra vs Malaga | 99.8min | 1289 | ✅ aligned |
| 3 | Burgos vs AD Ceuta FC | 122.6min | 999 | ✅ aligned |
| 4 | Huesca vs Cultural Leonesa | 121.8min | 1094 | ✅ aligned |
| 5 | Glasgow City W vs Hibernian W | 125.4min | 708 | ✅ aligned |
| 6 | Chelsea W vs Arsenal W | 117.2min | 998 | ✅ aligned |
| 7 | Racing Santander vs Sporting Gijon | 123.0min | 1133 | ✅ aligned |
| 8 | Rubio NU vs Club Guarani | 116.0min | 950 | ✅ aligned |
| 9 | Defensa Y Justicia vs Chaco For Ever D | 117.4min | 914 | ✅ aligned |
| 10 | Defensa Y Justicia vs Chaco For Ever | 111.5min | 914 | ✅ aligned |
| 11 | Sport Recife vs Vila Nova | 111.0min | 1144 | ✅ aligned |
| 12 | Internacional vs Sao Paulo | 117.4min | 1140 | ✅ aligned |
| 13 | Cruzeiro vs Vitoria | 119.5min | 1177 | ✅ aligned |
| 14 | Internacional vs Sao Paulo D | 87.4min | 1140 | ✅ aligned |
| 15 | Santa Fe vs Llaneros | 119.2min | 955 | ✅ aligned |
| 16 | Sportivo Trinidense vs Olimpia | 113.8min | 1192 | ✅ aligned |
| 17 | Atletico Nacional vs Cucuta | 120.7min | 1074 | ✅ aligned |
| 18 | Latina vs Potenza | 118.6min | 12 | ⚠️ 事件极少 |

**路径模板**: `/Volumes/990 PRO PCIe 4T/match_plan_recordings/2026-04-01/session_pgstapp_*/FT_*/`

**数据文件**:
- `*__full.mp4` — 完整比赛视频
- `*__live_events.jsonl` — 599 live text（已含 `_video_pos_sec`）
- `*__timeline.csv` — 数据站投注数据时间线
- `*__betting_data.jsonl` — 原始投注数据
- `manifest.json` — 录制元数据

**对齐字段说明** (`__live_events.jsonl` 中每行):
```json
{
  "code": 1029,
  "msgText": "进球！主队破门得分！",
  "time": "1832456",
  "_video_pos_sec": 1892.46,
  "_match_elapsed_sec": 1832.46,
  "_599_source": "live"
}
```
- `time`: 599原始时间（毫秒，比赛经过时间）
- `_match_elapsed_sec`: 比赛经过秒数（= time / 1000）
- `_video_pos_sec`: 该事件在视频中的位置（秒），可直接用于 VLM 帧定位

---

## 三、Tier-2：Desktop sessions 回补片段（8场有重叠）

03-24 老pipeline录制的短片段（1-5分钟），通过 599 API 回补了 live text 并做了对齐。

| 比赛 | 视频时长 | 599事件数 | 视频内事件 |
|---|---|---|---|
| Emmen vs Cambuur (session_122434) | 1min | 1197 | 32 |
| Emmen vs Cambuur (session_132002) | 5min | 1197 | 64 |
| Deportivo Pereira vs Cucuta (×6 sessions) | 1-5min | 945 | 14-73 |
| Fortaleza FC vs Deportivo Pasto | 5min | 952 | 39 |

**局限**: 视频极短（1-5min），只覆盖比赛中某个时间窗口，不适合完整比赛分析训练。  
**用途**: 可用于帧级 OCR/事件检测的小样本验证。

---

## 四、Tier-3：仅599文本数据（32场，无匹配视频）

外置盘 `_video_without_local_data` 目录下回补了599数据，但视频内容与比赛不重叠（视频录于赛前数小时）。

**回补成功的32场**:
- 国际友谊赛 (12场): 奥地利vs加纳, 约旦vs哥斯达黎加, 俄罗斯vs尼加拉瓜, 沙特vs埃及, 希腊vs巴拉圭, 匈牙利vs斯洛文尼亚, 苏格兰vs日本, 塞内加尔vs秘鲁, 海地vs突尼斯, 多米尼加vs古巴, 加拿大vs突尼斯, 伊拉克vs玻利维亚
- 欧洲联赛 (6场): 格拉纳达vs韦斯卡, 瓦拉多利德vs布尔戈斯, AC雷纳特vs诺瓦拉, 科隆女足vs法兰克福女足, 纳米比亚vs科摩罗, ASM奥兰vs特莱姆森
- 日本J联赛 (6场): 町田泽维亚vs川崎前锋, 奈良vs金泽, 甲府风林vs大宫松鼠, FC岐阜vs长野帕塞罗, 藤枝MYFCvs札幌冈萨多, 群马草津温泉vs枥木SC
- 南美 (4场): 库里科vs科比亚波, 托利马vs贾奎斯科尔多巴, 迈阿密FCvs罗德岛, 坦帕湾vs劳顿联
- 俄超/女足 (2场): 莫斯科鱼雷vsSKA哈巴罗夫斯克, MO比捷亚vsCA巴特纳
- 大洋洲女足 (2场): 中岸水手女足vs惠灵顿凤凰女足, 阿德莱德联女足vs新城堡联女足

**未匹配599的38场** (联赛覆盖不足): 韩国K联赛/K2, 日本低级别, 南非U23, 斯洛伐克, 埃塞俄比亚联赛等。

**用途**: 599文本数据可用于 NLP 事件分类/timeline 分析训练（不依赖视频）。

---

## 五、Tier-4：待回补的全场录像（294场 >= 30min）

03-24 ~ 03-31 的正常 session 中有 294 场 >= 30min 的全场录像，但都没有 599 数据。

| 日期 | 全场录像数 | 有599 | 可回补潜力 |
|---|---|---|---|
| 2026-03-24 | 2 | 0 | 低（老pipeline） |
| 2026-03-25 | 1 | 0 | 低 |
| 2026-03-26 | 33 | 0 | 中 |
| 2026-03-27 | 83 | 0 | 高（大量国际赛/联赛） |
| 2026-03-28 | 46 | 0 | 高 |
| 2026-03-29 | 27 | 0 | 高（pgstapp开始） |
| 2026-03-30 | 21 | 0 | 高 |
| 2026-03-31 | 61 | 0 | 高 |

**回补方法**: 运行 `backfill_599_live_text.py --all` 扫描有 timeline/betting_data 的 session，自动匹配599并对齐。这些session有完整录像且有本地数据（timeline.csv），对齐精度高。

**预估**: 有 timeline 的 session 约 200 场可回补（599足球覆盖率 ~70%），对齐后可直接升级为 Tier-1 级数据。

---

## 六、Gold Matches 库（58场）

`/Volumes/990 PRO PCIe 4T/match_plan_dataset_library/01_gold_matches/`

已有完整视频 + timeline，质量最高（人工筛选），但没有 599 数据。  
可通过 backfill 脚本回补599，升级为「视频+timeline+599」三重数据。

---

## 七、599 事件代码速查

| code | 含义 | 训练价值 |
|---|---|---|
| 10 | 上半场开球 | 锚点 |
| 13 | 下半场开球 | 锚点 |
| 20 | 终场哨 | 锚点 |
| 1029 | 主队进球 | ⭐ 关键事件 |
| 2053 | 客队进球 | ⭐ 关键事件 |
| 1005/2005 | 进球（旧码） | ⭐ 关键事件 |
| 1024/2048 | 中场组织进攻 | 场景 |
| 1025/2049 | 射门 | 关键事件 |
| 1026/2050 | 射门偏出 | 场景 |
| 1030/2054 | 角球 | 场景 |
| 1033/2057 | 犯规 | 场景 |
| 1034/2058 | 黄牌 | 关键事件 |
| 1035/2059 | 红牌 | 关键事件 |
| 1038/2062 | 换人 | 场景 |

---

## 八、下一步行动建议

### 立即可用（已完成 2026-04-02）
1. **155 场全场录像已对齐599** — 141,938 条事件，每场 ~30-120min 视频 + 600-1300 事件标注
2. 帧采样 + `_video_pos_sec` 定位 → 每帧关联最近的599事件 → 构建「帧→事件」训练对
3. 对齐精度：有进球的比赛通过 timeline 比分变化校准（精确到秒级），无进球的用599开球时间+timeline首行时间（误差约30-60秒）

### 中期
5. Gold matches 58 场回补 599 → 升级为三重数据
6. 持续录制新比赛（pgstapp 已自动集成599）

---

## 九、关键脚本清单

| 脚本 | 用途 |
|---|---|
| `recordings/backfill_599_live_text.py` | 599 回补主脚本（匹配+拉取+对齐） |
| `recordings/pion_gst_direct_chain/api_599_client.py` | 599 API 客户端 |
| `recordings/pion_gst_direct_chain/live_text_599.py` | 实时599集成（pgstapp pipeline用） |
| `recordings/data_site_node_prober.py` | sing-box 节点探测 & 自动选择 |
| `/tmp/align_599_video_v2.py` | 视频对齐脚本（v2，含timeline/manifest/dirname三级策略） |
| `/tmp/run_599_match_fast.py` | 快速599匹配脚本（英文队名→中文别名→599目录） |

---

## 十、别名库状态

- `recordings/team_aliases.json`: 10793行，覆盖主流联赛队名英中互译
- `recordings/team_alias_learned.json`: 5716行，系统自动学习的新别名
- `recordings/league_aliases.json`: 2363行，联赛名别名

**已知缺口**: 韩国K联赛、日本低级别联赛、南非U23、斯洛伐克低级别等小联赛队名覆盖不足（38场未匹配）。
