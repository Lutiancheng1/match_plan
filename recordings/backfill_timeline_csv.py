#!/usr/bin/env python3
"""Backfill timeline.csv for pgstapp sessions that only have betting_data.jsonl.

Scans recording date directories for session_pgstapp_* folders, finds FT_*
sub-directories with betting_data.jsonl but no timeline.csv, and generates
the missing timeline CSV using the same format as generate_sync_viewer.py.

Usage:
    python3 recordings/backfill_timeline_csv.py [--recordings-root /path/to/recordings] [--dates 2026-03-29 2026-03-30] [--dry-run]
"""
from __future__ import annotations

import argparse
import csv
import json
import re
from datetime import datetime, timezone
from pathlib import Path


DEFAULT_RECORDINGS_ROOT = Path("/Volumes/990 PRO PCIe 4T/match_plan_recordings")

TIMELINE_CSV_COLUMNS = [
    "video_pos_sec",       # 视频基准时间轴（秒），所有数据源对齐到此
    "match_time_sec",      # 比赛时钟（秒），从 RETIMESET 解析
    "match_half",          # 半场 (1=上半场, 2=下半场)
    "match_clock",         # 原始比赛时钟字符串 (1H^37:11)
    "score_h",
    "score_c",
    "game_phase",          # NOW_MODEL (HT/FT)
    "timestamp_utc",       # 数据采集 wall-clock
    "elapsed_sec",         # 采集偏移（从首次采集起）
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
]


def parse_iso(ts: str) -> datetime:
    return datetime.fromisoformat(ts.replace("Z", "+00:00"))


def hms_from_seconds(seconds: float) -> str:
    total = max(0, int(seconds))
    return f"{total // 3600:02d}:{(total % 3600) // 60:02d}:{total % 60:02d}"


def read_betting_jsonl(path: Path) -> list[dict]:
    rows = []
    with path.open(encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return rows


def _parse_retimeset(value: str) -> dict:
    """解析 RETIMESET (如 '1H^37:11') → {half, match_time_sec, match_time_ms}。"""
    text = str(value or "").strip()
    if not text:
        return {"half": 0, "match_time_sec": None, "match_time_ms": None}
    m = re.match(r"(\d)H\^(\d+):(\d{1,2})", text)
    if not m:
        return {"half": 0, "match_time_sec": None, "match_time_ms": None}
    half = int(m.group(1))
    total_sec = float(int(m.group(2)) * 60 + int(m.group(3)))
    return {"half": half, "match_time_sec": total_sec, "match_time_ms": int(total_sec * 1000)}


def build_timeline_rows(data_rows: list[dict]) -> list[dict]:
    """构建 timeline 行，以 video_pos_sec 为主时间轴。"""
    if not data_rows:
        return []
    start_ts = parse_iso(data_rows[0]["timestamp"])
    rows = []
    for row in data_rows:
        dt = parse_iso(row["timestamp"])
        fields = row.get("fields") or {}
        elapsed_sec = max(0.0, (dt - start_ts).total_seconds())
        retimeset = fields.get("RETIMESET", "")
        parsed = _parse_retimeset(retimeset)
        # 优先使用录制时已标注的 _video_pos_sec
        video_pos = row.get("_video_pos_sec")
        rows.append({
            "video_pos_sec": round(video_pos, 3) if video_pos is not None else "",
            "match_time_sec": parsed["match_time_sec"] if parsed["match_time_sec"] is not None else "",
            "match_half": parsed["half"] or "",
            "match_clock": retimeset,
            "score_h": row.get("score_h", ""),
            "score_c": row.get("score_c", ""),
            "game_phase": fields.get("NOW_MODEL", ""),
            "timestamp_utc": row.get("timestamp", ""),
            "elapsed_sec": round(elapsed_sec, 3),
            "gid": row.get("gid", ""),
            "ecid": row.get("ecid", ""),
            "league": fields.get("LEAGUE", ""),
            "team_h": row.get("team_h", ""),
            "team_c": row.get("team_c", ""),
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
        })
    return rows


def write_timeline_csv(timeline_rows: list[dict], output_path: Path) -> None:
    with output_path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=TIMELINE_CSV_COLUMNS)
        writer.writeheader()
        for row in timeline_rows:
            writer.writerow({k: row.get(k, "") for k in TIMELINE_CSV_COLUMNS})


def find_sessions_needing_timeline(recordings_root: Path, dates: list[str] | None) -> list[dict]:
    """Find FT sub-directories with betting_data but no timeline."""
    targets = []
    date_dirs = sorted(recordings_root.iterdir()) if not dates else [recordings_root / d for d in dates]

    for date_dir in date_dirs:
        if not date_dir.is_dir():
            continue
        for session_dir in sorted(date_dir.iterdir()):
            if not session_dir.is_dir():
                continue
            # Look for FT_* sub-directories
            for ft_dir in sorted(session_dir.iterdir()):
                if not ft_dir.is_dir() or not ft_dir.name.startswith("FT_"):
                    continue
                # Find betting_data.jsonl
                betting_files = list(ft_dir.glob("*betting_data.jsonl"))
                if not betting_files:
                    continue
                # Check if timeline already exists
                timeline_files = list(ft_dir.glob("*timeline.csv"))
                if timeline_files:
                    continue
                # Find video for naming
                video_files = list(ft_dir.glob("*__full.mp4"))
                stem = video_files[0].stem.replace("__full", "") if video_files else ft_dir.name
                targets.append({
                    "session_dir": str(session_dir),
                    "ft_dir": str(ft_dir),
                    "betting_data": str(betting_files[0]),
                    "timeline_output": str(ft_dir / f"{stem}__timeline.csv"),
                    "stem": stem,
                })
    return targets


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Backfill timeline.csv for pgstapp sessions.")
    parser.add_argument("--recordings-root", type=Path, default=DEFAULT_RECORDINGS_ROOT)
    parser.add_argument("--dates", nargs="*", help="Specific date dirs to scan (e.g. 2026-03-29 2026-03-30)")
    parser.add_argument("--dry-run", action="store_true", help="Only list what would be converted")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    targets = find_sessions_needing_timeline(args.recordings_root, args.dates)

    print(f"Found {len(targets)} FT directories needing timeline.csv")
    if not targets:
        return 0

    converted = 0
    skipped = 0
    for t in targets:
        betting_path = Path(t["betting_data"])
        output_path = Path(t["timeline_output"])
        data_rows = read_betting_jsonl(betting_path)

        if len(data_rows) < 2:
            print(f"  SKIP {t['stem']}: only {len(data_rows)} betting rows")
            skipped += 1
            continue

        if args.dry_run:
            print(f"  WOULD convert {t['stem']}: {len(data_rows)} rows -> {output_path.name}")
            continue

        timeline_rows = build_timeline_rows(data_rows)
        write_timeline_csv(timeline_rows, output_path)
        converted += 1
        print(f"  OK {t['stem']}: {len(timeline_rows)} rows -> {output_path.name}")

    print(f"\nDone: {converted} converted, {skipped} skipped")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
