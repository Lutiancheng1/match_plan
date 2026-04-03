#!/usr/bin/env python3
"""Build training samples from 599 live text events aligned to video.

Scans all __live_events.jsonl files, extracts frames at event timestamps,
and produces frame images + record JSONs + conversation JSONL for training.

Usage:
    python build_599_event_training_samples.py --dry-run   # preview only
    python build_599_event_training_samples.py              # full extraction
    python build_599_event_training_samples.py --p0-only    # goals + red cards only
"""
from __future__ import annotations

import argparse
import json
import random
import re
import subprocess
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

# ── 599 code → event_candidates label mapping ─────────────────────────
CODE_MAP = {
    # P0: critical events (match-changing)
    1029: ("goal", "P0"),             # 球进了!Goal~~~~ 主队
    2053: ("goal", "P0"),             # 球进了!Goal~~~~ 客队
    1032: ("red_card", "P0"),         # 噢!主队领到一张红牌
    2056: ("red_card", "P0"),         # 噢!客队领到一张红牌
    1031: ("penalty", "P0"),          # 哨响!主队获得一个点球
    2055: ("penalty", "P0"),          # 哨响!客队获得一个点球
    1060: ("penalty", "P0"),          # 噢!主队罚球射失了
    2084: ("penalty", "P0"),          # 噢!客队罚球射失了
    # P1: important events
    1034: ("heavy_contact_foul", "P1"),  # 主队领到一张黄牌
    2058: ("heavy_contact_foul", "P1"),  # 客队领到一张黄牌
    1025: ("corner_candidate", "P1"),    # 球出底线,主队获得角球
    2049: ("corner_candidate", "P1"),    # 球出底线,客队获得角球
    1039: ("dangerous_attack", "P1"),    # 射门!~~~（射正）
    2063: ("dangerous_attack", "P1"),    # 射门!~~~（射正）
    1040: ("dangerous_attack", "P1"),    # 射门!打偏了
    2064: ("dangerous_attack", "P1"),    # 射门!打偏了
    1041: ("dangerous_attack", "P1"),    # 射门!咣…皮球击中门框!
    2065: ("dangerous_attack", "P1"),    # 射门!咣…皮球击中门框!
    # P2: secondary events
    1043: ("offside", "P2"),             # 遗憾,主队稍稍越位了
    2067: ("offside", "P2"),             # 遗憾,客队稍稍越位了
    535: ("replay_sequence", "P2"),      # VAR事件
    1055: ("substitution", "P2"),        # 主队换人
    2079: ("substitution", "P2"),        # 客队换人
    1027: ("dangerous_attack", "P2"),    # 主队获得位置极佳的任意球
    2051: ("dangerous_attack", "P2"),    # 客队获得位置极佳的任意球
    132: ("injury_or_stoppage", "P2"),   # 场上有球员受伤,主裁判吹停了比赛
    # P3: frequent minor events
    1042: ("foul", "P3"),                # 主队犯规了（普通犯规）
    2066: ("foul", "P3"),                # 客队犯规了（普通犯规）
}

# Sampling limits per match by priority
PRIORITY_LIMITS = {
    "P0": {"max_events": 999, "offsets": [-4, -2, 0, 2, 4]},
    "P1": {"max_events": 20, "offsets": [-2, 0, 2]},
    "P2": {"max_events": 10, "offsets": [0]},
    "P3": {"max_events": 999, "offsets": [0]},  # 普通犯规，不限数量，1帧/事件
}

# Confidence decay by offset distance
OFFSET_CONFIDENCE = {0: 0.95, 2: 0.75, 4: 0.50}

RECORDINGS_BASE = Path("/Volumes/990 PRO PCIe 4T/match_plan_recordings")
OUTPUT_ROOT = Path("/Volumes/990 PRO PCIe 4T/match_plan_dataset_library/07_599_event_training")

SYSTEM_PROMPT = "你是直播画面分析助手。严格只输出纯 JSON，不要解释。"
USER_PROMPT = (
    "请分析这张足球直播画面截图，输出以下 JSON：\n"
    '{"scene_type": "...", "score_detected": "...", "match_clock_detected": "...", '
    '"scoreboard_visibility": "...", "replay_risk": "...", "tradeability": "...", '
    '"event_candidates": [{"label": "...", "confidence": ...}], '
    '"confidence": ..., "explanation_short": "..."}\n\n'
    "scene_type 可选: live_play, replay, scoreboard_focus, crowd_or_bench, stoppage, unknown\n"
    "event_candidates.label 可选: goal, red_card, penalty, ball_out_of_play, "
    "corner_candidate, dangerous_attack, celebration, replay_sequence, substitution, "
    "heavy_contact_foul, foul, offside, injury_or_stoppage, none"
)


def slugify(value: str) -> str:
    slug = re.sub(r"[^A-Za-z0-9]+", "_", value).strip("_")
    return slug[:120] or "unknown_match"


def find_video(match_dir: Path) -> Path | None:
    for f in match_dir.iterdir():
        if f.name.endswith("__full.mp4"):
            return f
    return None


def get_video_duration(match_dir: Path) -> float:
    manifest = match_dir / "manifest.json"
    if manifest.exists():
        try:
            return json.loads(manifest.read_text()).get("total_duration_sec", 0)
        except Exception:
            pass
    return 0


def extract_teams_from_dir(match_dir: Path) -> str:
    name = match_dir.name
    m = re.match(r"FT_(.+?)__", name)
    if m:
        return m.group(1).replace("_", " ")
    return name[:60]


def ffmpeg_extract_frame(video_path: Path, output_path: Path, at_sec: float) -> bool:
    if at_sec < 0:
        return False
    output_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        subprocess.check_call(
            ["ffmpeg", "-y", "-ss", f"{at_sec:.3f}", "-i", str(video_path),
             "-frames:v", "1", "-q:v", "2", str(output_path)],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            timeout=30,
        )
        if not output_path.exists():
            return False
        # Filter out black/loading frames: too small means no real content
        size = output_path.stat().st_size
        if size < 3000:
            output_path.unlink(missing_ok=True)
            return False
        return True
    except Exception:
        return False


def is_video_real_match(video_path: Path) -> bool:
    """Quick check: extract a frame at 5min mark to see if it's real footage.
    Data site loading screens produce tiny frames (<5KB).
    Real match footage is typically >15KB.
    """
    test_frame = Path(f"/tmp/_video_check_{video_path.parent.name[:40]}.jpg")
    try:
        subprocess.check_call(
            ["ffmpeg", "-y", "-ss", "300", "-i", str(video_path),
             "-frames:v", "1", "-q:v", "5", str(test_frame)],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            timeout=15,
        )
        if test_frame.exists():
            size = test_frame.stat().st_size
            test_frame.unlink(missing_ok=True)
            return size > 5000  # Real match frames are much larger
        return False
    except Exception:
        test_frame.unlink(missing_ok=True)
        return False


def build_match_clock(match_elapsed_sec: float) -> str:
    if match_elapsed_sec <= 0:
        return ""
    total_min = int(match_elapsed_sec / 60)
    sec = int(match_elapsed_sec % 60)
    return f"{total_min}:{sec:02d}"


def build_assistant_response(event_label: str, confidence: float,
                             msg_text: str, score_h: int | None,
                             score_g: int | None, match_clock: str) -> str:
    score = ""
    if score_h is not None and score_g is not None:
        score = f"{score_h}-{score_g}"

    obs = {
        "scene_type": "live_play",
        "score_detected": score,
        "match_clock_detected": match_clock,
        "scoreboard_visibility": "unknown",
        "replay_risk": "low",
        "tradeability": "tradeable",
        "event_candidates": [{"label": event_label, "confidence": round(confidence, 2)}],
        "confidence": round(confidence * 0.9, 2),
        "explanation_short": msg_text[:80] if msg_text else "",
    }
    return json.dumps(obs, ensure_ascii=False)


def build_neg_assistant_response() -> str:
    obs = {
        "scene_type": "live_play",
        "score_detected": "",
        "match_clock_detected": "",
        "scoreboard_visibility": "unknown",
        "replay_risk": "low",
        "tradeability": "tradeable",
        "event_candidates": [{"label": "none", "confidence": 0.95}],
        "confidence": 0.80,
        "explanation_short": "Normal play, no notable event.",
    }
    return json.dumps(obs, ensure_ascii=False)


def collect_all_match_dirs() -> list[Path]:
    dirs = []
    for date_dir in sorted(RECORDINGS_BASE.iterdir()):
        if not date_dir.name.startswith("2026-"):
            continue
        for ef in date_dir.rglob("*__live_events.jsonl"):
            md = ef.parent
            if find_video(md):
                dirs.append(md)
    return sorted(set(dirs))


def load_events(match_dir: Path) -> list[dict]:
    events = []
    for ef in match_dir.glob("*__live_events.jsonl"):
        for line in ef.read_text().splitlines():
            line = line.strip()
            if line:
                events.append(json.loads(line))
    return events


def select_events_for_sampling(events: list[dict], video_dur: float,
                                p0_only: bool = False) -> list[tuple[dict, str, str]]:
    """Select events eligible for frame sampling. Returns (event, label, priority)."""
    by_priority = defaultdict(list)

    for e in events:
        code = e.get("code")
        vps = e.get("_video_pos_sec")
        if code not in CODE_MAP or vps is None:
            continue
        if isinstance(vps, (int, float)) and 10 <= vps <= video_dur - 5:
            label, priority = CODE_MAP[code]
            if p0_only and priority != "P0":
                continue
            by_priority[priority].append((e, label, priority))

    selected = []
    for pri in ["P0", "P1", "P2", "P3"]:
        items = by_priority.get(pri, [])
        limit = PRIORITY_LIMITS[pri]["max_events"]
        if len(items) > limit:
            random.shuffle(items)
            items = items[:limit]
        selected.extend(items)

    return selected


def select_negative_positions(events: list[dict], video_dur: float,
                               target_count: int) -> list[float]:
    """Select video positions far from any event for negative samples."""
    event_times = sorted(
        e["_video_pos_sec"] for e in events
        if isinstance(e.get("_video_pos_sec"), (int, float)) and e["_video_pos_sec"] > 0
    )
    if not event_times:
        return []

    candidates = []
    for i in range(len(event_times) - 1):
        gap = event_times[i + 1] - event_times[i]
        if gap > 30:
            mid = (event_times[i] + event_times[i + 1]) / 2
            if 10 < mid < video_dur - 5:
                candidates.append(mid)

    if len(candidates) > target_count:
        random.shuffle(candidates)
        candidates = candidates[:target_count]

    return sorted(candidates)


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Build 599 event training samples")
    p.add_argument("--dry-run", action="store_true", help="Preview only, no frame extraction")
    p.add_argument("--p0-only", action="store_true", help="Only extract P0 events (goals + red cards)")
    p.add_argument("--output-root", type=Path, default=OUTPUT_ROOT)
    p.add_argument("--max-matches", type=int, default=0, help="Limit matches (0=all)")
    p.add_argument("--neg-ratio", type=float, default=1.0, help="Negative:positive sample ratio")
    p.add_argument("--seed", type=int, default=42)
    return p.parse_args()


def main() -> int:
    args = parse_args()
    random.seed(args.seed)
    output_root = args.output_root
    images_root = output_root / "images"
    records_root = output_root / "records"
    training_root = output_root / "training_data"

    if not args.dry_run:
        images_root.mkdir(parents=True, exist_ok=True)
        records_root.mkdir(parents=True, exist_ok=True)
        training_root.mkdir(parents=True, exist_ok=True)

    match_dirs = collect_all_match_dirs()
    if args.max_matches > 0:
        match_dirs = match_dirs[:args.max_matches]

    print(f"Found {len(match_dirs)} matches with video + 599 events")

    # Stats
    total_pos_frames = 0
    total_neg_frames = 0
    total_events_sampled = 0
    label_counts = Counter()
    priority_counts = Counter()
    match_stats = []
    all_conversations = []

    skipped_bad_video = 0
    for mi, md in enumerate(match_dirs):
        video = find_video(md)
        if not video:
            continue
        video_dur = get_video_duration(md)
        if video_dur < 1800:
            continue

        # Skip videos that are data site loading screens
        if not args.dry_run and not is_video_real_match(video):
            skipped_bad_video += 1
            continue

        teams = extract_teams_from_dir(md)
        match_slug = slugify(teams)
        events = load_events(md)

        selected = select_events_for_sampling(events, video_dur, p0_only=args.p0_only)
        if not selected:
            continue

        neg_count = int(len(selected) * args.neg_ratio)
        neg_positions = select_negative_positions(events, video_dur, neg_count)

        match_img_dir = images_root / match_slug
        match_rec_dir = records_root / match_slug

        pos_frames = 0
        neg_frames = 0

        # Extract positive (event) frames
        for ei, (evt, label, priority) in enumerate(selected):
            vps = evt["_video_pos_sec"]
            offsets = PRIORITY_LIMITS[priority]["offsets"]

            for off in offsets:
                target_sec = vps + off
                if target_sec < 0 or target_sec > video_dur:
                    continue

                conf = OFFSET_CONFIDENCE.get(abs(off), 0.4)
                frame_id = f"{match_slug}__evt_{evt['code']}_{int(vps)}s_off{off:+d}"
                img_path = match_img_dir / f"{frame_id}.jpg"

                if not args.dry_run:
                    ok = ffmpeg_extract_frame(video, img_path, target_sec)
                    if not ok:
                        continue

                match_clock = build_match_clock(evt.get("_match_elapsed_sec", 0) or 0)
                asst = build_assistant_response(
                    label, conf, evt.get("msgText", ""),
                    evt.get("homeScore"), evt.get("guestScore"), match_clock,
                )

                record = {
                    "frame_id": frame_id,
                    "match": teams,
                    "match_slug": match_slug,
                    "event_code": evt["code"],
                    "event_label": label,
                    "priority": priority,
                    "msg_text": evt.get("msgText", ""),
                    "video_pos_sec": round(target_sec, 2),
                    "sample_offset_sec": off,
                    "confidence": round(conf, 2),
                    "image_path": str(img_path),
                    "source_video": str(video),
                }

                if not args.dry_run:
                    match_rec_dir.mkdir(parents=True, exist_ok=True)
                    (match_rec_dir / f"{frame_id}.json").write_text(
                        json.dumps(record, ensure_ascii=False, indent=2)
                    )

                conv = {
                    "conversations": [
                        {"role": "system", "content": SYSTEM_PROMPT},
                        {"role": "user", "content": USER_PROMPT, "images": [str(img_path)]},
                        {"role": "assistant", "content": asst},
                    ],
                    "metadata": {
                        "source": "599_event_supervised",
                        "match": teams,
                        "event_code": evt["code"],
                        "event_label": label,
                        "priority": priority,
                        "video_pos_sec": round(target_sec, 2),
                        "sample_offset_sec": off,
                    },
                }
                all_conversations.append(conv)
                pos_frames += 1
                label_counts[label] += 1
                priority_counts[priority] += 1

            total_events_sampled += 1

        # Extract negative (no-event) frames
        for ni, neg_sec in enumerate(neg_positions):
            frame_id = f"{match_slug}__neg_{int(neg_sec)}s"
            img_path = match_img_dir / f"{frame_id}.jpg"

            if not args.dry_run:
                ok = ffmpeg_extract_frame(video, img_path, neg_sec)
                if not ok:
                    continue

            conv = {
                "conversations": [
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": USER_PROMPT, "images": [str(img_path)]},
                    {"role": "assistant", "content": build_neg_assistant_response()},
                ],
                "metadata": {
                    "source": "599_event_supervised",
                    "match": teams,
                    "event_code": 0,
                    "event_label": "none",
                    "priority": "NEG",
                    "video_pos_sec": round(neg_sec, 2),
                    "sample_offset_sec": 0,
                },
            }
            all_conversations.append(conv)

            if not args.dry_run:
                rec = {
                    "frame_id": frame_id,
                    "match": teams,
                    "match_slug": match_slug,
                    "event_code": 0,
                    "event_label": "none",
                    "priority": "NEG",
                    "video_pos_sec": round(neg_sec, 2),
                    "sample_offset_sec": 0,
                    "confidence": 0.95,
                    "image_path": str(img_path),
                    "source_video": str(video),
                }
                match_rec_dir.mkdir(parents=True, exist_ok=True)
                (match_rec_dir / f"{frame_id}.json").write_text(
                    json.dumps(rec, ensure_ascii=False, indent=2)
                )

            neg_frames += 1

        total_pos_frames += pos_frames
        total_neg_frames += neg_frames
        match_stats.append({
            "match": teams,
            "events_sampled": len(selected),
            "pos_frames": pos_frames,
            "neg_frames": neg_frames,
        })

        if (mi + 1) % 10 == 0 or mi < 5:
            print(f"  [{mi+1}/{len(match_dirs)}] {teams}: {len(selected)} events -> {pos_frames} pos + {neg_frames} neg frames")

    # Write conversations JSONL
    if not args.dry_run and all_conversations:
        random.shuffle(all_conversations)
        conv_path = training_root / "599_event_conversations_all.jsonl"
        with open(conv_path, "w", encoding="utf-8") as f:
            for c in all_conversations:
                f.write(json.dumps(c, ensure_ascii=False) + "\n")
        print(f"\nWritten: {conv_path} ({len(all_conversations)} records)")

    # Write manifest
    manifest = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "args": {
            "dry_run": args.dry_run,
            "p0_only": args.p0_only,
            "max_matches": args.max_matches,
            "neg_ratio": args.neg_ratio,
            "seed": args.seed,
        },
        "stats": {
            "matches_processed": len(match_stats),
            "total_events_sampled": total_events_sampled,
            "skipped_bad_video": skipped_bad_video,
            "total_pos_frames": total_pos_frames,
            "total_neg_frames": total_neg_frames,
            "total_frames": total_pos_frames + total_neg_frames,
            "label_distribution": dict(label_counts),
            "priority_distribution": dict(priority_counts),
        },
        "matches": match_stats,
    }

    if not args.dry_run:
        manifest_path = output_root / "manifests"
        manifest_path.mkdir(parents=True, exist_ok=True)
        (manifest_path / "sampling_manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2)
        )

    # Print summary
    print(f"\n{'='*60}")
    print(f"599 Event Training Samples {'(DRY RUN)' if args.dry_run else ''}")
    print(f"{'='*60}")
    print(f"  Matches:      {len(match_stats)}")
    if not args.dry_run:
        print(f"  Skipped (bad video): {skipped_bad_video}")
    print(f"  Events:       {total_events_sampled}")
    print(f"  Pos frames:   {total_pos_frames}")
    print(f"  Neg frames:   {total_neg_frames}")
    print(f"  Total frames: {total_pos_frames + total_neg_frames}")
    print(f"\n  Label distribution:")
    for label, count in sorted(label_counts.items(), key=lambda x: -x[1]):
        print(f"    {label:25s} {count:6d}")
    print(f"    {'none (negative)':25s} {total_neg_frames:6d}")
    print(f"\n  Priority distribution:")
    for pri, count in sorted(priority_counts.items()):
        print(f"    {pri}: {count}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
