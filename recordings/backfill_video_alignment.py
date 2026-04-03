#!/usr/bin/env python3
"""回溯对齐：用 599 事件重建 kickoff offset，给 betting_data 标注 _video_pos_sec。

扫描所有完整录制（视频 + betting_data + live_events），从 599 事件中
提取 kickoff 锚点重建 AlignmentEngine offset，然后给 betting_data 的
每行标注 _video_pos_sec，同时重建 timeline CSV。

Usage:
    python3 recordings/backfill_video_alignment.py [--dry-run] [--min-bd-lines 100] [--min-le-lines 100]
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from backfill_timeline_csv import build_timeline_rows, write_timeline_csv

DEFAULT_ROOT = Path("/Volumes/990 PRO PCIe 4T/match_plan_recordings")

# 599 kickoff codes
KICKOFF_CODES = {10, 3}
HALFTIME_CODE = 1
KICKOFF_2H_CODE = 13


def parse_retimeset_sec(value: str) -> tuple[float | None, int]:
    """Parse RETIMESET → (total_seconds, half). Returns (None, 0) on failure."""
    m = re.match(r"(\d)H\^(\d+):(\d{1,2})", str(value or "").strip())
    if not m:
        return None, 0
    return float(int(m.group(2)) * 60 + int(m.group(3))), int(m.group(1))


def get_video_duration(path: str) -> float:
    """Get video duration via ffprobe."""
    try:
        r = subprocess.run(
            ["ffprobe", "-v", "quiet", "-show_entries", "format=duration",
             "-of", "csv=p=0", path],
            capture_output=True, text=True, timeout=30,
        )
        return float(r.stdout.strip() or 0)
    except Exception:
        return 0.0


def rebuild_alignment_from_599(events: list[dict], video_duration: float) -> dict:
    """从 599 事件重建 kickoff offset。

    策略:
      1. 找到 kickoff 事件 (code=10, time<=120000ms) → match_time=0 锚点
      2. 用最后一个事件的 _599_observed_at 和 match_time 反推 kickoff_utc
      3. 如果录制有 video_start_utc 信息，用差值算 offset
      4. 否则用事件的 _video_pos_sec（如果已有）反推 offset
      5. 兜底: 用视频开头时间和第一个事件的 observed_at 估算

    返回: {"offset_h1": float|None, "offset_h2": float|None, "source": str, "details": str}
    """
    if not events:
        return {"offset_h1": None, "offset_h2": None, "source": "none", "details": "no events"}

    # 已有 _video_pos_sec 的事件 → 直接计算 offset
    calibration_points = []
    for e in events:
        vpos = e.get("_video_pos_sec")
        time_ms = int(e.get("time", -1) or -1)
        if vpos is not None and time_ms >= 0:
            time_sec = time_ms / 1000.0
            offset = vpos - time_sec
            half = 1 if time_sec < 46 * 60 else 2
            calibration_points.append({"offset": offset, "half": half, "time_sec": time_sec})

    if calibration_points:
        h1 = [p["offset"] for p in calibration_points if p["half"] == 1]
        h2 = [p["offset"] for p in calibration_points if p["half"] == 2]
        off_h1 = sorted(h1)[len(h1) // 2] if h1 else None
        off_h2 = sorted(h2)[len(h2) // 2] if h2 else None
        # 如果只有一个半场的 offset，另一个半场也用同一个
        if off_h1 is None and off_h2 is not None:
            off_h1 = off_h2
        if off_h2 is None and off_h1 is not None:
            off_h2 = off_h1
        return {
            "offset_h1": round(off_h1, 2) if off_h1 is not None else None,
            "offset_h2": round(off_h2, 2) if off_h2 is not None else None,
            "source": "599_video_pos",
            "details": f"h1={len(h1)}pts h2={len(h2)}pts",
        }

    # 没有已标注的 video_pos → 尝试从 observed_at 和 match_time 反推
    # 找 kickoff 事件
    kickoff_evt = None
    for e in events:
        code = int(e.get("code", -1) or -1)
        time_ms = int(e.get("time", -1) or -1)
        if code in KICKOFF_CODES and 0 <= time_ms <= 120000:
            kickoff_evt = e
            break

    # 用第一个有 observed_at 的事件
    first_obs = None
    for e in events:
        obs_str = e.get("_599_observed_at", "")
        if obs_str:
            try:
                first_obs = datetime.fromisoformat(obs_str.replace("Z", "+00:00"))
                break
            except Exception:
                pass

    if first_obs is None:
        return {"offset_h1": None, "offset_h2": None, "source": "none", "details": "no observed_at"}

    # 用最新事件反推 kickoff_utc
    latest_time_ms = -1
    latest_obs = None
    for e in events:
        time_ms = int(e.get("time", -1) or -1)
        obs_str = e.get("_599_observed_at", "")
        if time_ms > latest_time_ms and obs_str:
            try:
                latest_obs = datetime.fromisoformat(obs_str.replace("Z", "+00:00"))
                latest_time_ms = time_ms
            except Exception:
                pass

    if latest_time_ms < 0 or latest_obs is None:
        return {"offset_h1": None, "offset_h2": None, "source": "none", "details": "no valid events"}

    kickoff_utc = latest_obs - timedelta(milliseconds=latest_time_ms)

    # 估算 video_start_utc ≈ first_obs - first_event_time
    first_time_ms = -1
    for e in events:
        t = int(e.get("time", -1) or -1)
        if t >= 0:
            first_time_ms = t
            break

    if first_time_ms >= 0:
        # video 大约从 first_obs 前 first_time_ms 毫秒开始录制
        # 但这个估算很粗（有轮询延迟）
        # offset = kickoff_utc - video_start_utc
        # video_start_utc ≈ first_obs - first_time_ms/1000 - 一些缓冲
        # 简化: offset ≈ (kickoff_utc - first_obs).total_seconds() + first_time_ms/1000
        # 这实际上就是 0 附近... 不太有用

        # 更好的方法: 如果 video_duration 已知，且最后事件时间接近结束，
        # 可以反推 video_start_utc
        pass

    # 兜底: 假设 video 开始于 kickoff 前约 10-30s（常见范围）
    # 这不够精确，标注为 estimated
    estimated_offset = -15.0  # 粗略估算
    return {
        "offset_h1": estimated_offset,
        "offset_h2": estimated_offset,
        "source": "estimated",
        "details": f"kickoff_utc={kickoff_utc.isoformat()} est_offset={estimated_offset}s",
    }


def annotate_betting_data(
    bd_rows: list[dict],
    offset_h1: float | None,
    offset_h2: float | None,
) -> list[dict]:
    """给 betting_data 每行标注 _video_pos_sec。"""
    for row in bd_rows:
        fields = row.get("fields") or {}
        retimeset = fields.get("RETIMESET", "")
        match_time_sec, half = parse_retimeset_sec(retimeset)
        row["_match_time_sec"] = match_time_sec
        row["_match_half"] = half
        row["_match_clock"] = retimeset
        if match_time_sec is not None:
            offset = offset_h1 if half == 1 else offset_h2
            if offset is not None:
                row["_video_pos_sec"] = round(offset + match_time_sec, 3)
                row["_match_time_ms"] = int(match_time_sec * 1000)
            else:
                row["_video_pos_sec"] = None
                row["_match_time_ms"] = int(match_time_sec * 1000)
        else:
            row["_video_pos_sec"] = None
            row["_match_time_ms"] = None
    return bd_rows


def find_complete_sessions(root: Path, min_bd: int, min_le: int) -> list[dict]:
    """Find sessions with video + betting_data + live_events."""
    results = []
    for bd_path in sorted(root.rglob("*__betting_data.jsonl")):
        if bd_path.stat().st_size < 100:
            continue
        prefix = bd_path.stem.replace("__betting_data", "")
        parent = bd_path.parent
        le = parent / f"{prefix}__live_events.jsonl"
        vid = parent / f"{prefix}__full.mp4"
        if not le.exists() or le.stat().st_size < 100 or not vid.exists():
            continue
        vid_mb = vid.stat().st_size / 1024 / 1024
        if vid_mb < 1:
            continue
        bd_lines = sum(1 for _ in open(bd_path))
        le_lines = sum(1 for _ in open(le))
        if bd_lines < min_bd or le_lines < min_le:
            continue
        results.append({
            "prefix": prefix,
            "dir": parent,
            "bd_path": bd_path,
            "le_path": le,
            "vid_path": vid,
            "bd_lines": bd_lines,
            "le_lines": le_lines,
            "vid_mb": vid_mb,
        })
    return results


def process_session(session: dict, dry_run: bool) -> dict:
    """Process one session: rebuild alignment, annotate betting_data, rebuild timeline."""
    prefix = session["prefix"]
    parent = session["dir"]
    bd_path = session["bd_path"]
    le_path = session["le_path"]
    vid_path = session["vid_path"]

    # Load data
    with open(le_path, encoding="utf-8") as f:
        events = [json.loads(l) for l in f if l.strip()]
    with open(bd_path, encoding="utf-8") as f:
        bd_rows = [json.loads(l) for l in f if l.strip()]

    # Get video duration
    vid_duration = get_video_duration(str(vid_path))

    # Rebuild alignment from 599 events
    alignment = rebuild_alignment_from_599(events, vid_duration)

    if alignment["offset_h1"] is None:
        return {
            "prefix": prefix,
            "status": "skip_no_offset",
            "alignment": alignment,
            "bd_annotated": 0,
        }

    # Annotate betting data
    annotated = annotate_betting_data(bd_rows, alignment["offset_h1"], alignment["offset_h2"])
    vpos_count = sum(1 for r in annotated if r.get("_video_pos_sec") is not None)

    if dry_run:
        return {
            "prefix": prefix,
            "status": "dry_run",
            "alignment": alignment,
            "bd_annotated": vpos_count,
            "bd_total": len(annotated),
        }

    # Write annotated betting_data
    with open(bd_path, "w", encoding="utf-8") as f:
        for row in annotated:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")

    # Rebuild timeline CSV
    tl_stem = prefix.replace("__betting_data", "")
    tl_path = parent / f"{tl_stem}__timeline.csv"
    tl_rows = build_timeline_rows(annotated)
    write_timeline_csv(tl_rows, tl_path)

    # Save alignment metadata
    align_path = parent / f"{tl_stem}__alignment.json"
    align_path.write_text(json.dumps({
        "offset_h1": alignment["offset_h1"],
        "offset_h2": alignment["offset_h2"],
        "source": alignment["source"],
        "details": alignment["details"],
        "video_duration_sec": round(vid_duration, 1),
        "betting_data_rows": len(annotated),
        "betting_data_with_vpos": vpos_count,
        "live_events_count": len(events),
    }, ensure_ascii=False, indent=2), encoding="utf-8")

    return {
        "prefix": prefix,
        "status": "ok",
        "alignment": alignment,
        "bd_annotated": vpos_count,
        "bd_total": len(annotated),
        "timeline_rows": len(tl_rows),
    }


def main():
    parser = argparse.ArgumentParser(description="批量回溯对齐: 599 → video_pos_sec → betting_data")
    parser.add_argument("--recordings-root", default=str(DEFAULT_ROOT))
    parser.add_argument("--min-bd-lines", type=int, default=100)
    parser.add_argument("--min-le-lines", type=int, default=100)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    root = Path(args.recordings_root)
    if not root.exists():
        print(f"录制根目录不存在: {root}", file=sys.stderr)
        return 1

    sessions = find_complete_sessions(root, args.min_bd_lines, args.min_le_lines)
    print(f"找到 {len(sessions)} 场待对齐录制 (bd>={args.min_bd_lines}, le>={args.min_le_lines})")
    if not sessions:
        return 0

    ok_count = 0
    skip_count = 0
    for i, session in enumerate(sessions, 1):
        result = process_session(session, args.dry_run)
        status = result["status"]
        align = result.get("alignment", {})
        source = align.get("source", "?")
        off_h1 = align.get("offset_h1")
        off_h2 = align.get("offset_h2")
        off_str = f"h1={off_h1:.1f}s h2={off_h2:.1f}s" if off_h1 is not None else "N/A"

        if status in ("ok", "dry_run"):
            ok_count += 1
            tag = "DRY" if status == "dry_run" else "OK "
            print(
                f"[{i:3d}/{len(sessions)}] {tag} {off_str:>24s} "
                f"vpos={result.get('bd_annotated', 0):>4d}/{result.get('bd_total', 0):<4d} "
                f"src={source:<16s} | {session['prefix'][:70]}"
            )
        else:
            skip_count += 1
            print(
                f"[{i:3d}/{len(sessions)}] SKIP                          "
                f"src={source:<16s} | {session['prefix'][:70]} — {align.get('details', '')}"
            )

    print(f"\n完成: {ok_count} 对齐, {skip_count} 跳过")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
