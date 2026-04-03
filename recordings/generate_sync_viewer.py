#!/usr/bin/env python3
"""
generate_sync_viewer.py
=======================

为录制完成的 session 生成“视频 + 右侧同步数据面板”的本地 HTML 查看页。

特点:
  - 左侧视频，右侧实时数据卡片
  - 视频播放 / 暂停 / 拖动时，右侧数据跟随同一时间轴
  - 展示最近 10 次盘口/比分变化，并附精确到秒的时间
  - 纯本地静态 HTML，无需后端服务

用法:
  python3 generate_sync_viewer.py /path/to/session_dir
"""

from __future__ import annotations

import argparse
import csv
import html
import json
import os
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path


TRACKED_CHANGE_FIELDS = [
    ("score_h", "主队比分"),
    ("score_c", "客队比分"),
    ("ratio_re", "让球"),
    ("ior_reh", "让球主队赔率"),
    ("ior_rec", "让球客队赔率"),
    ("ratio_rouo", "大小球"),
    ("ior_rouh", "大球赔率"),
    ("ior_rouc", "小球赔率"),
    ("ior_rmh", "主胜赔率"),
    ("ior_rmn", "平局赔率"),
    ("ior_rmc", "客胜赔率"),
    ("redcard_h", "主队红牌"),
    ("redcard_c", "客队红牌"),
]

HANDICAP_CHANGE_FIELDS = {"ratio_re", "ior_reh", "ior_rec"}
OU_CHANGE_FIELDS = {"ratio_rouo", "ior_rouh", "ior_rouc"}


def parse_iso(ts: str) -> datetime:
    return datetime.fromisoformat(ts.replace("Z", "+00:00"))


def hms_from_seconds(seconds: float) -> str:
    total = max(0, int(seconds))
    return f"{total // 3600:02d}:{(total % 3600) // 60:02d}:{total % 60:02d}"


def format_match_clock(raw: str) -> str:
    text = str(raw or "").strip()
    if not text:
        return "-"
    upper = text.upper()
    if upper in {"HT", "FT"}:
        return upper

    # Source values look like "1H^39:42" / "2H^79:20":
    # the "1H/2H" prefix is a phase marker, while the minute:second
    # portion is already the match clock we want to display.
    match = re.match(r"(?i)^(?P<phase>\d+)H(?:\s*[\^ ]\s*)?(?P<minutes>\d{1,3}):(?P<seconds>\d{2})$", text)
    if match:
        minutes = int(match.group("minutes"))
        return f"{minutes:02d}:{match.group('seconds')}"

    text = re.sub(r"\s+", " ", text.replace("^", " ")).strip()
    return text


def short_ts(ts: str) -> str:
    dt = parse_iso(ts)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone().strftime("%H:%M:%S")


def read_jsonl(path: Path):
    rows = []
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            rows.append(json.loads(line))
    return rows


def _parse_retimeset_sec(value):
    """解析 RETIMESET → 比赛时间秒数 (None if unparseable)。"""
    import re as _re
    m = _re.match(r"(\d)H\^(\d+):(\d{1,2})", str(value or "").strip())
    if not m:
        return None, 0
    return float(int(m.group(2)) * 60 + int(m.group(3))), int(m.group(1))


def build_timeline_rows(data_rows):
    if not data_rows:
        return []

    start_ts = parse_iso(data_rows[0]["timestamp"])
    rows = []
    for row in data_rows:
        dt = parse_iso(row["timestamp"])
        fields = row.get("fields", {}) or {}
        elapsed_sec = max(0.0, (dt - start_ts).total_seconds())
        match_time_sec, match_half = _parse_retimeset_sec(fields.get("RETIMESET", ""))
        # 优先使用录制时标注的 _video_pos_sec
        video_pos = row.get("_video_pos_sec")
        rows.append({
            "video_pos_sec": round(video_pos, 3) if video_pos is not None else "",
            "match_time_sec": match_time_sec if match_time_sec is not None else "",
            "match_half": match_half or "",
            "elapsed_sec": round(elapsed_sec, 3),
            "elapsed_hms": hms_from_seconds(elapsed_sec),
            "timestamp_utc": row.get("timestamp", ""),
            "timestamp_label": short_ts(row.get("timestamp", "")),
            "gid": row.get("gid", ""),
            "ecid": row.get("ecid", ""),
            "league": fields.get("LEAGUE", ""),
            "team_h": row.get("team_h", ""),
            "team_c": row.get("team_c", ""),
            "score_h": row.get("score_h", ""),
            "score_c": row.get("score_c", ""),
            "score": f"{row.get('score_h', '')}-{row.get('score_c', '')}",
            "match_clock": fields.get("RETIMESET", ""),
            "match_clock_label": format_match_clock(fields.get("RETIMESET", "")),
            "game_phase": fields.get("NOW_MODEL", ""),
            "redcard_h": fields.get("REDCARD_H", ""),
            "redcard_c": fields.get("REDCARD_C", ""),
            "ratio_re": fields.get("RATIO_RE", ""),
            "ior_reh": fields.get("IOR_REH", ""),
            "ior_rec": fields.get("IOR_REC", ""),
            "ratio_rouo": fields.get("RATIO_ROUO", ""),
            "ior_rouh": fields.get("IOR_ROUH", ""),
            "ior_rouc": fields.get("IOR_ROUC", ""),
            "ior_rmh": fields.get("IOR_RMH", ""),
            "ior_rmn": fields.get("IOR_RMN", ""),
            "ior_rmc": fields.get("IOR_RMC", ""),
            "fields": fields,
        })
    return rows


def write_timeline_csv(timeline_rows, output_path: Path):
    with output_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow([
            "video_pos_sec",
            "match_time_sec",
            "match_half",
            "match_clock",
            "score_h",
            "score_c",
            "game_phase",
            "elapsed_sec",
            "elapsed_hms",
            "timestamp_utc",
            "gid",
            "ecid",
            "league",
            "team_h",
            "team_c",
            "redcard_h",
            "redcard_c",
            "ratio_re",
            "ior_reh",
            "ior_rec",
            "ratio_rouo",
            "ior_rouh",
            "ior_rouc",
            "ior_rmh",
            "ior_rmn",
            "ior_rmc",
        ])
        for row in timeline_rows:
            writer.writerow([
                row.get("video_pos_sec", ""),
                row.get("match_time_sec", ""),
                row.get("match_half", ""),
                row["match_clock"],
                row["score_h"],
                row["score_c"],
                row["game_phase"],
                row["elapsed_sec"],
                row["elapsed_hms"],
                row["timestamp_utc"],
                row["gid"],
                row["ecid"],
                row["league"],
                row["team_h"],
                row["team_c"],
                row["redcard_h"],
                row["redcard_c"],
                row["ratio_re"],
                row["ior_reh"],
                row["ior_rec"],
                row["ratio_rouo"],
                row["ior_rouh"],
                row["ior_rouc"],
                row["ior_rmh"],
                row["ior_rmn"],
                row["ior_rmc"],
            ])


def build_change_events(timeline_rows):
    if not timeline_rows:
        return []

    events = []
    previous = None
    for row in timeline_rows:
        if previous is None:
            events.append({
                "elapsed_sec": row["elapsed_sec"],
                "elapsed_hms": row["elapsed_hms"],
                "timestamp_utc": row["timestamp_utc"],
                "timestamp_label": row["timestamp_label"],
                "match_clock": row["match_clock"],
                "match_clock_label": row["match_clock_label"],
                "kind": "initial",
                "summary": "初始数据快照",
                "changes": [],
            })
            previous = row
            continue

        changes = []
        for key, label in TRACKED_CHANGE_FIELDS:
            before = previous.get(key, "")
            after = row.get(key, "")
            if str(before) != str(after):
                changes.append({
                    "field": key,
                    "label": label,
                    "before": before,
                    "after": after,
                })

        if changes:
            score_changed = any(change["field"] in ("score_h", "score_c") for change in changes)
            summary = "比分/盘口更新" if score_changed else "盘口更新"
            events.append({
                "elapsed_sec": row["elapsed_sec"],
                "elapsed_hms": row["elapsed_hms"],
                "timestamp_utc": row["timestamp_utc"],
                "timestamp_label": row["timestamp_label"],
                "match_clock": row["match_clock"],
                "match_clock_label": row["match_clock_label"],
                "kind": "change",
                "summary": summary,
                "changes": changes,
            })

        previous = row

    return events


def mutate_numeric_text(text: str, delta: float) -> str:
    text = str(text or "").strip()
    if not text:
        return text
    if not re.fullmatch(r"-?\d+(?:\.\d+)?", text):
        return text
    decimals = len(text.split(".", 1)[1]) if "." in text else 0
    value = float(text)
    updated = max(0.01, value + delta)
    return f"{updated:.{decimals}f}" if decimals else str(int(round(updated)))


def build_preview_market_events(base_row, field_triplet, target_count, existing_count, summary):
    needed = max(0, target_count - existing_count)
    if needed <= 0:
        return []

    latest_ts = parse_iso(base_row["timestamp_utc"])
    latest_elapsed = float(base_row["elapsed_sec"])
    preview_events = []
    deltas = [-0.12, 0.08, -0.05, 0.11, -0.07, 0.09, -0.04, 0.06, -0.03, 0.05]

    for idx in range(needed):
        seconds_back = float((needed - idx) * 2)
        elapsed_sec = max(0.0, latest_elapsed - seconds_back)
        event_ts = (latest_ts - timedelta(seconds=seconds_back)).isoformat()

        home_or_over = mutate_numeric_text(base_row.get(field_triplet[1], ""), deltas[idx % len(deltas)])
        away_or_under = mutate_numeric_text(base_row.get(field_triplet[2], ""), -deltas[idx % len(deltas)])

        preview_events.append({
            "elapsed_sec": round(elapsed_sec, 3),
            "elapsed_hms": hms_from_seconds(elapsed_sec),
            "timestamp_utc": event_ts,
            "timestamp_label": short_ts(event_ts),
            "match_clock": base_row.get("match_clock", ""),
            "match_clock_label": base_row.get("match_clock_label", "-"),
            "kind": "preview",
            "summary": summary,
            "changes": [
                {
                    "field": field_triplet[1],
                    "label": field_triplet[1],
                    "before": base_row.get(field_triplet[1], ""),
                    "after": home_or_over,
                },
                {
                    "field": field_triplet[2],
                    "label": field_triplet[2],
                    "before": base_row.get(field_triplet[2], ""),
                    "after": away_or_under,
                },
            ],
        })
    return preview_events


def build_preview_change_events(timeline_rows, change_events, target_per_market=10):
    if not timeline_rows:
        return change_events

    base_row = timeline_rows[-1]
    handicap_existing = sum(
        1 for event in change_events
        if any(change["field"] in HANDICAP_CHANGE_FIELDS for change in event.get("changes", []))
    )
    ou_existing = sum(
        1 for event in change_events
        if any(change["field"] in OU_CHANGE_FIELDS for change in event.get("changes", []))
    )

    preview_events = list(change_events)
    preview_events.extend(
        build_preview_market_events(
            base_row,
            ("ratio_re", "ior_reh", "ior_rec"),
            target_per_market,
            handicap_existing,
            "预览让球变动",
        )
    )
    preview_events.extend(
        build_preview_market_events(
            base_row,
            ("ratio_rouo", "ior_rouh", "ior_rouc"),
            target_per_market,
            ou_existing,
            "预览大小球变动",
        )
    )
    preview_events.sort(key=lambda item: item["elapsed_sec"])
    return preview_events


def discover_stream_artifacts(session_dir: Path, stream_entry: dict):
    merged_video = Path(stream_entry.get("merged_video", ""))
    if not merged_video.exists():
        raise FileNotFoundError(f"视频不存在: {merged_video}")

    match_dir = merged_video.parent
    betting_files = sorted(match_dir.glob("*__betting_data.jsonl"))
    if not betting_files:
        raise FileNotFoundError(f"未找到 betting_data.jsonl: {match_dir}")
    betting_path = betting_files[0]

    return {
        "match_dir": match_dir,
        "merged_video": merged_video,
        "betting_data": betting_path,
    }


def html_shell(title: str, video_src: str, dashboard_payload: dict) -> str:
    payload_json = json.dumps(dashboard_payload, ensure_ascii=False)
    safe_title = html.escape(title)
    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>{safe_title}</title>
  <style>
    :root {{
      --bg: #f3efe6;
      --panel: #fffaf0;
      --panel-strong: #f8f0de;
      --ink: #1b1c17;
      --muted: #676457;
      --line: #ddd3be;
      --accent: #0d6b52;
      --accent-2: #bd4f2a;
      --accent-3: #1f4068;
      --score: #20251e;
      --chip: #ece4d2;
      --shadow: 0 22px 45px rgba(72, 57, 27, 0.14);
      --radius: 20px;
      --mono: "SF Mono", "Menlo", "Monaco", monospace;
      --sans: "SF Pro Display", "PingFang SC", "Helvetica Neue", sans-serif;
    }}
    * {{
      box-sizing: border-box;
    }}
    body {{
      margin: 0;
      font-family: var(--sans);
      color: var(--ink);
      background:
        radial-gradient(circle at top left, rgba(13, 107, 82, 0.12), transparent 32%),
        radial-gradient(circle at bottom right, rgba(189, 79, 42, 0.14), transparent 28%),
        linear-gradient(180deg, #f9f5ec 0%, var(--bg) 100%);
      min-height: 100vh;
    }}
    .page {{
      width: min(1720px, calc(100vw - 28px));
      margin: 14px auto;
      display: grid;
      grid-template-columns: minmax(700px, 1.08fr) minmax(500px, 0.92fr);
      gap: 16px;
      align-items: start;
    }}
    .video-shell,
    .panel-shell {{
      background: rgba(255, 250, 240, 0.92);
      border: 1px solid rgba(157, 137, 95, 0.2);
      border-radius: 26px;
      box-shadow: var(--shadow);
      backdrop-filter: blur(16px);
    }}
    .video-shell {{
      padding: 18px;
      position: sticky;
      top: 16px;
    }}
    .video-topbar {{
      display: flex;
      justify-content: space-between;
      align-items: baseline;
      gap: 16px;
      margin-bottom: 14px;
    }}
    .eyebrow {{
      display: inline-flex;
      align-items: center;
      gap: 8px;
      padding: 7px 12px;
      border-radius: 999px;
      background: var(--chip);
      color: var(--muted);
      font-size: 12px;
      letter-spacing: 0.08em;
      text-transform: uppercase;
    }}
    .eyebrow::before {{
      content: "";
      width: 8px;
      height: 8px;
      border-radius: 999px;
      background: var(--accent);
      box-shadow: 0 0 0 5px rgba(13, 107, 82, 0.12);
    }}
    h1 {{
      margin: 10px 0 0;
      font-size: clamp(28px, 4vw, 40px);
      line-height: 1.05;
      letter-spacing: -0.03em;
    }}
    .subtitle {{
      color: var(--muted);
      margin-top: 6px;
      font-size: 15px;
    }}
    video {{
      width: 100%;
      display: block;
      border-radius: 20px;
      background: #000;
      box-shadow: inset 0 0 0 1px rgba(255,255,255,0.08);
    }}
    .transport {{
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 12px;
      margin-top: 12px;
      flex-wrap: wrap;
    }}
    .transport-group {{
      display: flex;
      gap: 10px;
      flex-wrap: wrap;
    }}
    button {{
      border: 0;
      background: #16372d;
      color: white;
      border-radius: 999px;
      padding: 10px 14px;
      font-weight: 600;
      cursor: pointer;
      transition: transform 120ms ease, background 120ms ease;
    }}
    button:hover {{
      transform: translateY(-1px);
      background: #114e3c;
    }}
    .panel-shell {{
      padding: 14px;
      display: grid;
      gap: 10px;
    }}
    .card {{
      background: linear-gradient(180deg, #fffdf8, var(--panel));
      border: 1px solid var(--line);
      border-radius: var(--radius);
      padding: 12px;
    }}
    .top-strip {{
      display: grid;
      gap: 8px;
    }}
    .top-chip {{
      padding: 10px 12px;
      border-radius: 14px;
      border: 1px solid rgba(157, 137, 95, 0.18);
      background: var(--panel-strong);
      font-family: var(--mono);
      font-size: 12px;
      line-height: 1.3;
      color: var(--score);
    }}
    .score-chip {{
      background: linear-gradient(135deg, #1a201a 0%, #2f2e21 100%);
      color: #f8f2e7;
      border-color: rgba(255,255,255,0.06);
      font-size: 13px;
      font-weight: 700;
    }}
    .market-grid {{
      display: grid;
      grid-template-columns: repeat(2, minmax(0, 1fr));
      gap: 14px;
    }}
    .market-card {{
      padding: 16px 18px;
      min-width: 0;
      overflow: hidden;
      background:
        radial-gradient(circle at top right, rgba(13, 107, 82, 0.08), transparent 36%),
        linear-gradient(180deg, #fffdf8 0%, #fbf4e7 100%);
    }}
    .market-card.ou-card {{
      background:
        radial-gradient(circle at top right, rgba(31, 64, 104, 0.08), transparent 38%),
        linear-gradient(180deg, #fffdf8 0%, #f3f1ea 100%);
    }}
    .market-head {{
      display: grid;
      gap: 8px;
      margin-bottom: 10px;
      padding-bottom: 10px;
      border-bottom: 1px dashed rgba(157, 137, 95, 0.28);
    }}
    .market-topline {{
      display: flex;
      align-items: baseline;
      justify-content: space-between;
      gap: 12px;
    }}
    .market-title {{
      color: var(--score);
      font-size: 20px;
      font-weight: 700;
      letter-spacing: -0.01em;
      line-height: 1.15;
    }}
    .market-line {{
      color: var(--muted);
      font-size: 13px;
      font-weight: 700;
      white-space: nowrap;
      text-align: right;
    }}
    .market-line span {{
      color: var(--accent);
      font-family: var(--mono);
      margin-left: 6px;
      font-size: 19px;
    }}
    .ou-card .market-line span {{
      color: var(--accent-3);
    }}
    .market-current {{
      display: flex;
      align-items: baseline;
      gap: 10px;
      font-family: var(--mono);
      color: var(--muted);
      font-size: 13px;
      font-weight: 700;
    }}
    .market-current strong {{
      color: var(--score);
      font-size: 17px;
      font-weight: 700;
    }}
    .market-table {{
      width: 100%;
      border-collapse: collapse;
      table-layout: fixed;
      font-family: var(--mono);
      font-size: 14px;
      line-height: 1.28;
    }}
    .market-table col.col-time {{
      width: 36%;
    }}
    .market-table col.col-odds {{
      width: 32%;
    }}
    .market-table th,
    .market-table td {{
      padding: 7px 6px;
      border-bottom: 1px dashed rgba(157, 137, 95, 0.14);
      vertical-align: top;
    }}
    .market-table th {{
      color: var(--muted);
      font-size: 14px;
      font-weight: 700;
      text-align: left;
      white-space: nowrap;
    }}
    .market-table td {{
      color: var(--score);
      white-space: nowrap;
    }}
    .market-table tr.sample td {{
      color: #6f6a5d;
    }}
    .market-table td.time {{
      color: var(--accent);
      font-weight: 700;
      white-space: nowrap;
    }}
    .market-table th:not(:first-child),
    .market-table td:not(:first-child) {{
      text-align: right;
    }}
    .table-empty {{
      color: var(--muted);
      text-align: center;
      padding: 12px 6px;
    }}
    @media (max-width: 1080px) {{
      .page {{
        grid-template-columns: 1fr;
      }}
      .video-shell {{
        position: static;
      }}
      .market-grid {{
        grid-template-columns: 1fr;
      }}
      .market-head {{
        flex-direction: column;
        align-items: flex-start;
      }}
    }}
  </style>
</head>
<body>
  <div class="page">
    <section class="video-shell">
      <div class="video-topbar">
        <div>
          <div class="eyebrow">Synchronized Viewer</div>
          <h1 id="page-title">{safe_title}</h1>
          <div class="subtitle" id="page-subtitle">视频播放、暂停、拖动时，右侧数据同步更新。</div>
        </div>
      </div>
      <video id="video" controls preload="metadata" src="{html.escape(video_src)}"></video>
      <div class="transport">
        <div class="transport-group">
          <button type="button" data-seek="-5">回退 5 秒</button>
          <button type="button" data-seek="5">前进 5 秒</button>
        </div>
        <div class="transport-group">
          <button type="button" id="toggle-play">播放 / 暂停</button>
        </div>
      </div>
    </section>

    <aside class="panel-shell">
      <div class="eyebrow">Live Data Mirror</div>
      <div class="top-strip">
        <div class="top-chip score-chip" id="score-summary">主队 0 : 0 客队 | - / -</div>
        <div class="top-chip" id="sync-summary">视频 00:00:00，对应数据 00:00:00，最近更新 --:--:--</div>
      </div>

      <div class="market-grid">
        <div class="card market-card">
          <div class="market-head">
            <div class="market-topline">
              <div class="market-title">让球</div>
              <div class="market-line">当前盘口 <span id="ratio-re">-</span></div>
            </div>
            <div class="market-current" id="reh-current">最新 <strong>主 -</strong> <strong>客 -</strong></div>
          </div>
          <table class="market-table">
            <colgroup>
              <col class="col-time" />
              <col class="col-odds" />
              <col class="col-odds" />
            </colgroup>
            <thead>
              <tr>
                <th>比赛</th>
                <th>主</th>
                <th>客</th>
              </tr>
            </thead>
            <tbody id="history-handicap"></tbody>
          </table>
        </div>

        <div class="card market-card ou-card">
          <div class="market-head">
            <div class="market-topline">
              <div class="market-title">大小球</div>
              <div class="market-line">当前盘口 <span id="ratio-rouo">-</span></div>
            </div>
            <div class="market-current" id="rou-current">最新 <strong>大 -</strong> <strong>小 -</strong></div>
          </div>
          <table class="market-table">
            <colgroup>
              <col class="col-time" />
              <col class="col-odds" />
              <col class="col-odds" />
            </colgroup>
            <thead>
              <tr>
                <th>比赛</th>
                <th>大</th>
                <th>小</th>
              </tr>
            </thead>
            <tbody id="history-ou"></tbody>
          </table>
        </div>
      </div>
    </aside>
  </div>

  <script>
    const DASHBOARD_DATA = {payload_json};
    const video = document.getElementById('video');
    const handicapHistory = document.getElementById('history-handicap');
    const ouHistory = document.getElementById('history-ou');

    const fields = {{
      syncSummary: document.getElementById('sync-summary'),
      scoreSummary: document.getElementById('score-summary'),
      ratioRe: document.getElementById('ratio-re'),
      ratioRouo: document.getElementById('ratio-rouo'),
      rehCurrent: document.getElementById('reh-current'),
      rouCurrent: document.getElementById('rou-current'),
    }};

    document.querySelectorAll('[data-seek]').forEach((button) => {{
      button.addEventListener('click', () => {{
        const delta = Number(button.dataset.seek || '0');
        video.currentTime = Math.max(0, Math.min(video.duration || Infinity, video.currentTime + delta));
        syncToVideo();
      }});
    }});
    document.getElementById('toggle-play').addEventListener('click', () => {{
      if (video.paused) {{
        video.play();
      }} else {{
        video.pause();
      }}
    }});

    function fmtSeconds(sec) {{
      const total = Math.max(0, Math.floor(sec || 0));
      const h = String(Math.floor(total / 3600)).padStart(2, '0');
      const m = String(Math.floor((total % 3600) / 60)).padStart(2, '0');
      const s = String(total % 60).padStart(2, '0');
      return `${{h}}:${{m}}:${{s}}`;
    }}

    function findRowIndex(timeSec) {{
      const rows = DASHBOARD_DATA.timeline;
      let left = 0;
      let right = rows.length - 1;
      let best = 0;
      while (left <= right) {{
        const mid = Math.floor((left + right) / 2);
        if (rows[mid].elapsed_sec <= timeSec + 0.0001) {{
          best = mid;
          left = mid + 1;
        }} else {{
          right = mid - 1;
        }}
      }}
      return best;
    }}

    function collectMarketHistory(currentTime, fieldNames) {{
      return DASHBOARD_DATA.change_events
        .filter((event) => event.elapsed_sec <= currentTime + 0.0001)
        .map((event) => {{
          const values = {{}};
          (event.changes || []).forEach((change) => {{
            if (fieldNames.includes(change.field)) {{
              values[change.field] = change.after || '-';
            }}
          }});
          if (!Object.keys(values).length) {{
            return null;
          }}
          return {{
            time_label: event.match_clock_label || event.timestamp_label || '-',
            values,
          }};
        }})
        .filter(Boolean)
        .slice(-10)
        .reverse();
    }}

    function collectMarketSamples(currentTime, fieldNames, limit) {{
      const timeline = DASHBOARD_DATA.timeline || [];
      const past = timeline.filter((row) => row.elapsed_sec <= currentTime + 0.0001);
      const future = timeline.filter((row) => row.elapsed_sec > currentTime + 0.0001);
      const chosen = past.slice(-limit);
      if (chosen.length < limit) {{
        chosen.push(...future.slice(0, limit - chosen.length));
      }}
      return chosen.reverse().map((row) => {{
        const values = {{}};
        fieldNames.forEach((field) => {{
          values[field] = row[field] || '-';
        }});
        return {{
          time_label: row.match_clock_label || row.timestamp_label || '-',
          values,
          sample: true,
        }};
      }});
    }}

    function renderMarketTable(currentTime, element, config) {{
      const fieldNames = config.columns.map((column) => column.field);
      const items = collectMarketHistory(
        currentTime,
        fieldNames,
      );
      const usedTimes = new Set(items.map((item) => item.time_label));
      if (items.length < 10) {{
        const needed = 10 - items.length;
        const samples = collectMarketSamples(currentTime, fieldNames, 20)
          .filter((item) => !usedTimes.has(item.time_label))
          .slice(0, needed);
        items.push(...samples);
      }}

      if (!items.length) {{
        element.innerHTML = `<tr><td colspan="${{config.columns.length + 1}}" class="table-empty">${{config.emptyText}}</td></tr>`;
        return;
      }}

      element.innerHTML = items.map((item) => `
        <tr class="${{item.sample ? 'sample' : 'change'}}">
          <td class="time">${{item.time_label}}</td>
          ${{config.columns.map((column) => `<td>${{item.values[column.field] || '-'}}</td>`).join('')}}
        </tr>
      `).join('');
    }}

    function syncToVideo() {{
      if (!DASHBOARD_DATA.timeline.length) {{
        return;
      }}

      const currentTime = video.currentTime || 0;
      const row = DASHBOARD_DATA.timeline[findRowIndex(currentTime)];

      fields.syncSummary.textContent = `比赛 ${{row.match_clock_label || '-'}} | 抓取 ${{row.timestamp_label || '--:--:--'}} | 视频偏移 ${{fmtSeconds(currentTime)}}`;
      fields.scoreSummary.textContent = `${{row.team_h || DASHBOARD_DATA.meta.team_h || '主队'}} ${{row.score_h || '0'}} : ${{row.score_c || '0'}} ${{row.team_c || DASHBOARD_DATA.meta.team_c || '客队'}} | ${{row.match_clock_label || '-'}} / ${{row.game_phase || '-'}}`;
      fields.ratioRe.textContent = row.ratio_re || '-';
      fields.ratioRouo.textContent = row.ratio_rouo || '-';
      fields.rehCurrent.innerHTML = `最新 <strong>主 ${{row.ior_reh || '-'}}</strong> <strong>客 ${{row.ior_rec || '-'}}</strong>`;
      fields.rouCurrent.innerHTML = `最新 <strong>大 ${{row.ior_rouh || '-'}}</strong> <strong>小 ${{row.ior_rouc || '-'}}</strong>`;

      renderMarketTable(
        currentTime,
        handicapHistory,
        {{
          emptyText: '暂无变动',
          columns: [
            {{ field: 'ior_reh', label: '主' }},
            {{ field: 'ior_rec', label: '客' }},
          ],
        }}
      );
      renderMarketTable(
        currentTime,
        ouHistory,
        {{
          emptyText: '暂无变动',
          columns: [
            {{ field: 'ior_rouh', label: '大' }},
            {{ field: 'ior_rouc', label: '小' }},
          ],
        }}
      );
    }}

    video.addEventListener('loadedmetadata', () => {{
      if (DASHBOARD_DATA.meta.preview_fill) {{
        video.currentTime = Math.max(0, (video.duration || 0) - 0.25);
      }}
      syncToVideo();
    }});
    ['timeupdate', 'seeking', 'seeked', 'pause', 'play'].forEach((name) => {{
      video.addEventListener(name, syncToVideo);
    }});
    syncToVideo();
  </script>
</body>
</html>
"""


def generate_stream_viewer(session_dir: Path, stream_entry: dict, preview_fill: int = 0) -> Path:
    artifacts = discover_stream_artifacts(session_dir, stream_entry)
    data_rows = read_jsonl(artifacts["betting_data"])
    timeline_rows = build_timeline_rows(data_rows)
    if not timeline_rows:
        raise ValueError(f"未读取到可用时间线数据: {artifacts['betting_data']}")

    timeline_csv = artifacts["match_dir"] / (
        artifacts["merged_video"].stem.replace("__full", "__timeline") + ".csv"
    )
    write_timeline_csv(timeline_rows, timeline_csv)

    change_events = build_change_events(timeline_rows)
    if preview_fill > 0:
        change_events = build_preview_change_events(
            timeline_rows,
            change_events,
            target_per_market=preview_fill,
        )
    meta = {
        "session_id": session_dir.name,
        "match_label": stream_entry.get("teams", stream_entry.get("match_id", "match")),
        "league": timeline_rows[0].get("league", ""),
        "team_h": timeline_rows[0].get("team_h", ""),
        "team_c": timeline_rows[0].get("team_c", ""),
        "gid": timeline_rows[0].get("gid", ""),
        "ecid": timeline_rows[0].get("ecid", ""),
        "timeline_count": len(timeline_rows),
        "change_count": len(change_events),
        "preview_fill": preview_fill,
    }

    viewer_payload = {
        "meta": meta,
        "timeline": timeline_rows,
        "change_events": change_events,
    }

    stem = artifacts["merged_video"].stem.replace("__full", "__sync_viewer")
    if preview_fill > 0:
        stem += "_preview"
    html_path = artifacts["match_dir"] / f"{stem}.html"
    html_path.write_text(
        html_shell(meta["match_label"], artifacts["merged_video"].name, viewer_payload),
        encoding="utf-8",
    )
    return html_path


def generate_session_index(session_dir: Path, html_files):
    cards = []
    for html_file in html_files:
        label = html.escape(html_file.stem.replace("__sync_viewer", ""))
        rel = html.escape(html_file.relative_to(session_dir).as_posix())
        cards.append(
            f'<a class="card" href="{rel}"><strong>{label}</strong><span>打开同步查看页</span></a>'
        )

    index_html = f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>Session Sync Viewers</title>
  <style>
    body {{
      margin: 0;
      font-family: "SF Pro Display", "PingFang SC", sans-serif;
      background: linear-gradient(180deg, #f7f3ea, #efe8d9);
      color: #1d1b16;
      min-height: 100vh;
      display: grid;
      place-items: center;
    }}
    main {{
      width: min(900px, calc(100vw - 40px));
      display: grid;
      gap: 18px;
    }}
    h1 {{
      margin: 0;
      font-size: 42px;
      letter-spacing: -0.04em;
    }}
    p {{
      margin: 0;
      color: #6a6557;
    }}
    .grid {{
      display: grid;
      gap: 14px;
      grid-template-columns: repeat(auto-fit, minmax(280px, 1fr));
    }}
    .card {{
      display: grid;
      gap: 8px;
      padding: 18px;
      border-radius: 20px;
      text-decoration: none;
      color: inherit;
      background: rgba(255,255,255,0.78);
      border: 1px solid rgba(128, 109, 69, 0.18);
      box-shadow: 0 20px 40px rgba(54, 38, 8, 0.12);
    }}
    .card span {{
      color: #6a6557;
    }}
  </style>
</head>
<body>
  <main>
    <div>
      <h1>同步查看页</h1>
      <p>打开任一比赛，即可看到左侧视频和右侧随时间联动的数据面板。</p>
    </div>
    <section class="grid">
      {"".join(cards)}
    </section>
  </main>
</body>
</html>
"""
    index_path = session_dir / "session_sync_viewers.html"
    index_path.write_text(index_html, encoding="utf-8")
    return index_path


def generate_session_viewers(session_dir: Path, preview_fill: int = 0):
    result_path = session_dir / "session_result.json"
    if not result_path.exists():
        raise FileNotFoundError(f"未找到 session_result.json: {result_path}")

    result = json.loads(result_path.read_text(encoding="utf-8"))
    html_files = []
    skipped = []
    for stream_entry in result.get("streams", []):
        if not stream_entry.get("merged_video"):
            continue
        try:
            html_files.append(generate_stream_viewer(session_dir, stream_entry, preview_fill=preview_fill))
        except Exception as exc:
            skipped.append({
                "match_id": stream_entry.get("match_id", ""),
                "error": str(exc),
            })

    if not html_files:
        detail = skipped[0]["error"] if skipped else "无可用视频"
        raise ValueError(f"当前 session 没有可生成 viewer 的 merged_video: {session_dir} ({detail})")

    index_path = generate_session_index(session_dir, html_files)
    return html_files, index_path


def main():
    parser = argparse.ArgumentParser(description="为录制 session 生成同步查看页")
    parser.add_argument("session_dir", help="session 目录路径")
    parser.add_argument("--preview-fill", type=int, default=0, help="不足时补足预览变动条数")
    args = parser.parse_args()

    session_dir = Path(args.session_dir).expanduser().resolve()
    html_files, index_path = generate_session_viewers(session_dir, preview_fill=args.preview_fill)
    print(f"INDEX={index_path}")
    for html_file in html_files:
        print(f"VIEWER={html_file}")


if __name__ == "__main__":
    main()
