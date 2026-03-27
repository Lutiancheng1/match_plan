#!/usr/bin/env python3
"""Create reusable sample clips from Gold-quality materials."""

from __future__ import annotations

import argparse
import csv
import json
import subprocess
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any


DEFAULT_DATASET_ROOT = Path("/Volumes/990 PRO PCIe 4T/match_plan_dataset_library")
DEFAULT_GOLD_MANIFEST = DEFAULT_DATASET_ROOT / "01_gold_matches" / "current_gold_matches.json"
DEFAULT_CLIP_ROOT = DEFAULT_DATASET_ROOT / "04_golden_samples" / "clips"
DEFAULT_LABEL_ROOT = DEFAULT_DATASET_ROOT / "04_golden_samples" / "labels"
DEFAULT_META_ROOT = DEFAULT_DATASET_ROOT / "04_golden_samples" / "meta"

SIGNAL_FIELDS = [
    "score_h",
    "score_c",
    "redcard_h",
    "redcard_c",
    "ratio_re",
    "ior_reh",
    "ior_rec",
    "ratio_rouo",
    "ior_rouh",
    "ior_rouc",
]


@dataclass
class ClipCandidate:
    kind: str
    reason: str
    start_sec: float
    end_sec: float
    pivot_elapsed: float
    row_index: int

    @property
    def duration_sec(self) -> float:
        return round(self.end_sec - self.start_sec, 3)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Auto-cut reusable clips from Gold-quality recording materials."
    )
    parser.add_argument("--dataset-root", default=str(DEFAULT_DATASET_ROOT))
    parser.add_argument("--gold-manifest", default=str(DEFAULT_GOLD_MANIFEST))
    parser.add_argument("--clip-root", default=str(DEFAULT_CLIP_ROOT))
    parser.add_argument("--label-root", default=str(DEFAULT_LABEL_ROOT))
    parser.add_argument("--meta-root", default=str(DEFAULT_META_ROOT))
    parser.add_argument("--pre-seconds", type=float, default=5.0)
    parser.add_argument("--post-seconds", type=float, default=8.0)
    parser.add_argument("--calm-duration", type=float, default=12.0)
    parser.add_argument("--calm-gap-seconds", type=float, default=180.0)
    parser.add_argument("--overlap-merge-gap", type=float, default=8.0)
    parser.add_argument("--limit-per-match", type=int, default=24)
    return parser.parse_args()


def slugify(value: str) -> str:
    import re

    slug = re.sub(r"[^A-Za-z0-9]+", "_", value).strip("_")
    return slug[:120] or "unknown_match"


def ffprobe_duration(video_path: Path) -> float:
    output = subprocess.check_output(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "default=noprint_wrappers=1:nokey=1",
            str(video_path),
        ],
        text=True,
    ).strip()
    return float(output)


def load_timeline_rows(timeline_path: Path) -> list[dict[str, str]]:
    with timeline_path.open(newline="") as handle:
        return list(csv.DictReader(handle))


def timeline_value(row: dict[str, str], key: str) -> str:
    return (row.get(key) or "").strip()


def detect_change_events(
    rows: list[dict[str, str]],
    *,
    duration_sec: float,
    pre_seconds: float,
    post_seconds: float,
) -> list[ClipCandidate]:
    candidates: list[ClipCandidate] = []
    prev: dict[str, str] | None = None
    for idx, row in enumerate(rows):
        elapsed = float(row["elapsed_sec"])
        if prev is None:
            prev = row
            continue
        changes: list[str] = []
        for key in SIGNAL_FIELDS:
            if timeline_value(prev, key) != timeline_value(row, key):
                changes.append(key)
        if changes:
            start_sec = max(0.0, elapsed - pre_seconds)
            end_sec = min(duration_sec, elapsed + post_seconds)
            if end_sec - start_sec >= 2.0:
                candidates.append(
                    ClipCandidate(
                        kind="event",
                        reason=",".join(changes),
                        start_sec=round(start_sec, 3),
                        end_sec=round(end_sec, 3),
                        pivot_elapsed=round(elapsed, 3),
                        row_index=idx,
                    )
                )
        prev = row
    return candidates


def detect_calm_windows(
    rows: list[dict[str, str]],
    *,
    duration_sec: float,
    calm_duration: float,
    calm_gap_seconds: float,
) -> list[ClipCandidate]:
    candidates: list[ClipCandidate] = []
    if not rows:
        return candidates
    starts = [0.0]
    current = calm_gap_seconds
    while current + calm_duration <= duration_sec:
        starts.append(current)
        current += calm_gap_seconds
    for idx, start_sec in enumerate(starts):
        end_sec = min(duration_sec, start_sec + calm_duration)
        if end_sec - start_sec < 5.0:
            continue
        candidates.append(
            ClipCandidate(
                kind="calm",
                reason="steady_reference",
                start_sec=round(start_sec, 3),
                end_sec=round(end_sec, 3),
                pivot_elapsed=round(start_sec, 3),
                row_index=0,
            )
        )
    return candidates


def dedupe_candidates(
    candidates: list[ClipCandidate], *, overlap_merge_gap: float, limit_per_match: int
) -> list[ClipCandidate]:
    ordered = sorted(candidates, key=lambda item: (item.start_sec, item.end_sec))
    kept: list[ClipCandidate] = []
    last_end = -9999.0
    for candidate in ordered:
        if candidate.start_sec <= last_end + overlap_merge_gap:
            # Prefer event clips over calm references when windows overlap.
            if kept and kept[-1].kind == "calm" and candidate.kind == "event":
                kept[-1] = candidate
                last_end = candidate.end_sec
            continue
        kept.append(candidate)
        last_end = candidate.end_sec
        if len(kept) >= limit_per_match:
            break
    return kept


def nearest_timeline_row(rows: list[dict[str, str]], pivot_elapsed: float) -> dict[str, str]:
    nearest = rows[0]
    min_gap = abs(float(nearest["elapsed_sec"]) - pivot_elapsed)
    for row in rows[1:]:
        gap = abs(float(row["elapsed_sec"]) - pivot_elapsed)
        if gap < min_gap:
            nearest = row
            min_gap = gap
    return nearest


def ffmpeg_cut(input_video: Path, output_video: Path, start_sec: float, duration_sec: float) -> None:
    output_video.parent.mkdir(parents=True, exist_ok=True)
    subprocess.check_call(
        [
            "ffmpeg",
            "-y",
            "-ss",
            f"{start_sec:.3f}",
            "-i",
            str(input_video),
            "-t",
            f"{duration_sec:.3f}",
            "-an",
            "-c:v",
            "libx264",
            "-preset",
            "veryfast",
            "-crf",
            "18",
            str(output_video),
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def build_match_clips(
    material: dict[str, Any],
    *,
    clip_root: Path,
    label_root: Path,
    meta_root: Path,
    pre_seconds: float,
    post_seconds: float,
    calm_duration: float,
    calm_gap_seconds: float,
    overlap_merge_gap: float,
    limit_per_match: int,
) -> dict[str, Any]:
    teams = material["teams"]
    slug = slugify(teams)
    video_path = Path(material["video"])
    timeline_path = Path(material["timeline"])
    duration_sec = ffprobe_duration(video_path)
    rows = load_timeline_rows(timeline_path)

    event_candidates = detect_change_events(
        rows,
        duration_sec=duration_sec,
        pre_seconds=pre_seconds,
        post_seconds=post_seconds,
    )
    calm_candidates = detect_calm_windows(
        rows,
        duration_sec=duration_sec,
        calm_duration=calm_duration,
        calm_gap_seconds=calm_gap_seconds,
    )
    candidates = dedupe_candidates(
        event_candidates + calm_candidates,
        overlap_merge_gap=overlap_merge_gap,
        limit_per_match=limit_per_match,
    )

    match_clip_dir = clip_root / slug
    match_label_dir = label_root / slug
    match_meta_dir = meta_root / slug
    for folder in (match_clip_dir, match_label_dir, match_meta_dir):
        if folder.exists():
            for child in folder.iterdir():
                if child.is_file():
                    child.unlink()
                else:
                    import shutil

                    shutil.rmtree(child)
        folder.mkdir(parents=True, exist_ok=True)

    clip_items: list[dict[str, Any]] = []
    for index, candidate in enumerate(candidates, start=1):
        clip_id = f"{slug}__{index:03d}"
        clip_path = match_clip_dir / f"{clip_id}.mp4"
        label_path = match_label_dir / f"{clip_id}.json"
        meta_path = match_meta_dir / f"{clip_id}.json"
        ffmpeg_cut(video_path, clip_path, candidate.start_sec, candidate.duration_sec)
        nearest = nearest_timeline_row(rows, candidate.pivot_elapsed)
        label_payload = {
            "clip_id": clip_id,
            "teams": teams,
            "source_match_id": material.get("match_id") or "",
            "kind": candidate.kind,
            "reason": candidate.reason,
            "manual_review_status": "pending",
            "labels": {
                "score_h": nearest.get("score_h") or "",
                "score_c": nearest.get("score_c") or "",
                "match_clock": nearest.get("match_clock") or "",
                "game_phase": nearest.get("game_phase") or "",
                "scene_type": "",
                "event_candidates": [],
                "strong_event": False,
            },
        }
        meta_payload = {
            "clip_id": clip_id,
            "teams": teams,
            "quality_tier": material["quality_tier"],
            "source_video": str(video_path),
            "source_timeline": str(timeline_path),
            "source_viewer": material.get("viewer") or "",
            "start_sec": candidate.start_sec,
            "end_sec": candidate.end_sec,
            "duration_sec": candidate.duration_sec,
            "pivot_elapsed": candidate.pivot_elapsed,
            "row_index": candidate.row_index,
            "nearest_timeline_row": nearest,
            "created_at": datetime.now().isoformat(timespec="seconds"),
        }
        label_path.write_text(json.dumps(label_payload, ensure_ascii=False, indent=2) + "\n")
        meta_path.write_text(json.dumps(meta_payload, ensure_ascii=False, indent=2) + "\n")
        clip_items.append(
            {
                "clip_id": clip_id,
                "clip_path": str(clip_path),
                "label_path": str(label_path),
                "meta_path": str(meta_path),
                "kind": candidate.kind,
                "reason": candidate.reason,
                "start_sec": candidate.start_sec,
                "end_sec": candidate.end_sec,
                "duration_sec": candidate.duration_sec,
            }
        )

    manifest = {
        "teams": teams,
        "source_video": str(video_path),
        "source_timeline": str(timeline_path),
        "coverage_ratio": material["coverage_ratio"],
        "clip_count": len(clip_items),
        "clips": clip_items,
    }
    (match_meta_dir / "clips_manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n"
    )
    return manifest


def main() -> None:
    args = parse_args()
    dataset_root = Path(args.dataset_root)
    gold_manifest_path = Path(args.gold_manifest)
    clip_root = Path(args.clip_root)
    label_root = Path(args.label_root)
    meta_root = Path(args.meta_root)

    dataset_root.mkdir(parents=True, exist_ok=True)
    for path in (clip_root, label_root, meta_root):
        path.mkdir(parents=True, exist_ok=True)

    materials = json.loads(gold_manifest_path.read_text())
    manifests = []
    for material in materials:
        manifests.append(
            build_match_clips(
                material,
                clip_root=clip_root,
                label_root=label_root,
                meta_root=meta_root,
                pre_seconds=args.pre_seconds,
                post_seconds=args.post_seconds,
                calm_duration=args.calm_duration,
                calm_gap_seconds=args.calm_gap_seconds,
                overlap_merge_gap=args.overlap_merge_gap,
                limit_per_match=args.limit_per_match,
            )
        )
    output = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "gold_match_count": len(materials),
        "total_clip_count": sum(item["clip_count"] for item in manifests),
        "matches": manifests,
    }
    summary_path = meta_root / "current_golden_sample_manifest.json"
    summary_path.write_text(json.dumps(output, ensure_ascii=False, indent=2) + "\n")
    print(summary_path)
    print(f"matches={len(materials)} clips={output['total_clip_count']}")


if __name__ == "__main__":
    main()
