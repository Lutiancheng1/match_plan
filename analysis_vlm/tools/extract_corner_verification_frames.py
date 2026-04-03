#!/usr/bin/env python3
"""提取角球事件前后的视频帧，用于人工/VLM验证599延迟。

扫描已对齐录制中的599角球事件(code=1025/2049)，在事件vpos前后
提取多帧截图，输出到指定目录，生成验证清单CSV。

Usage:
    python3 analysis_vlm/tools/extract_corner_verification_frames.py \
        --recordings-root /Volumes/990\ PRO\ PCIe\ 4T/match_plan_recordings \
        --output-dir /tmp/corner_verification \
        --max-matches 10 \
        --corners-per-match 3
"""
from __future__ import annotations

import argparse
import csv
import json
import subprocess
import sys
from pathlib import Path

CORNER_CODES = {1025, 2049}
CORNER_LABELS = {1025: "HOME_corner", 2049: "AWAY_corner"}

# 帧提取偏移（秒），相对于599报告的vpos
# 基于验证发现：角球准备在vpos-20s附近，执行在vpos-8s，599报告在vpos
FRAME_OFFSETS = [-24, -20, -16, -12, -8, -4, -2, 0, 2, 5]


def get_video_duration(path: str) -> float:
    try:
        r = subprocess.run(
            ["ffprobe", "-v", "quiet", "-show_entries", "format=duration",
             "-of", "csv=p=0", path],
            capture_output=True, text=True, timeout=30,
        )
        return float(r.stdout.strip() or 0)
    except Exception:
        return 0.0


def extract_frame(video_path: str, time_sec: float, output_path: str) -> bool:
    try:
        subprocess.run(
            ["ffmpeg", "-y", "-ss", str(time_sec), "-i", video_path,
             "-frames:v", "1", "-q:v", "2", output_path],
            capture_output=True, timeout=30,
        )
        return Path(output_path).exists() and Path(output_path).stat().st_size > 1000
    except Exception:
        return False


def find_corner_sessions(root: Path, max_matches: int, min_vid_mb: float = 50.0) -> list[dict]:
    """找到有角球事件且vpos合理的录制。"""
    results = []
    for le_path in sorted(root.rglob("*__live_events.jsonl")):
        if le_path.stat().st_size < 500:
            continue
        prefix = le_path.stem.replace("__live_events", "")
        vid = le_path.parent / f"{prefix}__full.mp4"
        if not vid.exists():
            continue
        vid_mb = vid.stat().st_size / 1024 / 1024
        if vid_mb < min_vid_mb:
            continue

        corners = []
        with open(le_path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    evt = json.loads(line)
                except json.JSONDecodeError:
                    continue
                code = int(evt.get("code", -1) or -1)
                if code in CORNER_CODES:
                    vpos = evt.get("_video_pos_sec")
                    if vpos is not None and isinstance(vpos, (int, float)) and vpos > 30:
                        corners.append(evt)

        if len(corners) >= 2:
            results.append({
                "prefix": prefix[:80],
                "le_path": le_path,
                "vid_path": vid,
                "vid_mb": vid_mb,
                "corners": corners,
            })
            if len(results) >= max_matches:
                break

    return results


def process_session(
    session: dict,
    output_dir: Path,
    corners_per_match: int,
    dry_run: bool,
) -> list[dict]:
    """对一场比赛提取角球验证帧。"""
    prefix = session["prefix"]
    vid_path = str(session["vid_path"])
    corners = session["corners"]
    vid_dur = get_video_duration(vid_path)

    # 选择分布均匀的角球（上半场+下半场各取一些）
    selected = corners[:corners_per_match]

    records = []
    for ci, evt in enumerate(selected):
        code = int(evt.get("code", -1))
        vpos = float(evt["_video_pos_sec"])
        time_ms = int(evt.get("time", 0) or 0)
        label = CORNER_LABELS.get(code, "unknown")
        match_time_str = f"{time_ms // 60000}:{(time_ms // 1000) % 60:02d}"

        match_dir = output_dir / prefix[:60]
        match_dir.mkdir(parents=True, exist_ok=True)

        for off in FRAME_OFFSETS:
            target_sec = vpos + off
            if target_sec < 0 or target_sec > vid_dur:
                continue

            frame_name = f"corner{ci + 1}_{label}_vpos{int(vpos)}_off{off:+d}s.jpg"
            frame_path = match_dir / frame_name

            if dry_run:
                records.append({
                    "match": prefix[:60],
                    "corner_idx": ci + 1,
                    "corner_type": label,
                    "event_code": code,
                    "match_time": match_time_str,
                    "vpos_raw": round(vpos, 2),
                    "offset_sec": off,
                    "target_vpos": round(target_sec, 2),
                    "frame_path": str(frame_path),
                    "extracted": "dry_run",
                    "broadcast_clock": "",
                    "visible_corner": "",
                    "notes": "",
                })
                continue

            ok = extract_frame(vid_path, target_sec, str(frame_path))
            records.append({
                "match": prefix[:60],
                "corner_idx": ci + 1,
                "corner_type": label,
                "event_code": code,
                "match_time": match_time_str,
                "vpos_raw": round(vpos, 2),
                "offset_sec": off,
                "target_vpos": round(target_sec, 2),
                "frame_path": str(frame_path),
                "extracted": "ok" if ok else "fail",
                # 以下字段留空，由人工/VLM 标注
                "broadcast_clock": "",
                "visible_corner": "",
                "notes": "",
            })

    return records


def main():
    parser = argparse.ArgumentParser(
        description="提取角球事件前后帧，用于验证599延迟"
    )
    parser.add_argument("--recordings-root",
                        default="/Volumes/990 PRO PCIe 4T/match_plan_recordings")
    parser.add_argument("--output-dir", default="/tmp/corner_verification")
    parser.add_argument("--max-matches", type=int, default=10)
    parser.add_argument("--corners-per-match", type=int, default=3)
    parser.add_argument("--min-vid-mb", type=float, default=50.0)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    root = Path(args.recordings_root)
    if not root.exists():
        print(f"录制根目录不存在: {root}", file=sys.stderr)
        return 1

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    sessions = find_corner_sessions(root, args.max_matches, args.min_vid_mb)
    print(f"找到 {len(sessions)} 场有角球的录制")

    all_records = []
    for i, session in enumerate(sessions, 1):
        n_corners = min(len(session["corners"]), args.corners_per_match)
        print(f"[{i}/{len(sessions)}] {session['prefix'][:50]}  "
              f"({n_corners} corners, {session['vid_mb']:.0f}MB)")
        records = process_session(
            session, output_dir, args.corners_per_match, args.dry_run
        )
        all_records.extend(records)
        ok = sum(1 for r in records if r["extracted"] == "ok")
        print(f"  -> {ok}/{len(records)} frames extracted")

    # 写验证清单CSV
    csv_path = output_dir / "corner_verification_manifest.csv"
    if all_records:
        fieldnames = list(all_records[0].keys())
        with open(csv_path, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(all_records)
        print(f"\n验证清单: {csv_path}")
        print(f"总计: {len(all_records)} 帧 from {len(sessions)} 场比赛")
    else:
        print("没有提取到帧")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
