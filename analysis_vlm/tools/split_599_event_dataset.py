#!/usr/bin/env python3
"""Split 599 event training data into train/val/test by match.

Ensures no data leakage: all frames from same match stay in same split.
Balances P0 events across splits.

Usage:
    python split_599_event_dataset.py
    python split_599_event_dataset.py --train-ratio 0.8 --val-ratio 0.1
"""
from __future__ import annotations

import argparse
import json
import random
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

INPUT_JSONL = Path("/Volumes/990 PRO PCIe 4T/match_plan_dataset_library/07_599_event_training/training_data/599_event_conversations_all.jsonl")
OUTPUT_DIR = Path("/Volumes/990 PRO PCIe 4T/match_plan_dataset_library/07_599_event_training/training_data")


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser()
    p.add_argument("--input", type=Path, default=INPUT_JSONL)
    p.add_argument("--output-dir", type=Path, default=OUTPUT_DIR)
    p.add_argument("--train-ratio", type=float, default=0.80)
    p.add_argument("--val-ratio", type=float, default=0.10)
    p.add_argument("--seed", type=int, default=42)
    return p.parse_args()


def main() -> int:
    args = parse_args()
    random.seed(args.seed)
    test_ratio = 1.0 - args.train_ratio - args.val_ratio

    # Load all conversations
    records = []
    with open(args.input, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                records.append(json.loads(line))

    print(f"Loaded {len(records)} records")

    # Group by match
    by_match: dict[str, list[dict]] = defaultdict(list)
    for r in records:
        match = r.get("metadata", {}).get("match", "unknown")
        by_match[match].append(r)

    matches = list(by_match.keys())
    random.shuffle(matches)

    # Identify matches with P0 events (goals/red cards) for balanced allocation
    p0_matches = [m for m in matches if any(
        r["metadata"].get("priority") == "P0" for r in by_match[m]
    )]
    non_p0_matches = [m for m in matches if m not in set(p0_matches)]

    def split_list(items, train_r, val_r):
        n = len(items)
        n_train = max(1, int(n * train_r))
        n_val = max(1, int(n * val_r)) if n > 2 else 0
        return items[:n_train], items[n_train:n_train + n_val], items[n_train + n_val:]

    p0_train, p0_val, p0_test = split_list(p0_matches, args.train_ratio, args.val_ratio)
    np_train, np_val, np_test = split_list(non_p0_matches, args.train_ratio, args.val_ratio)

    train_matches = set(p0_train + np_train)
    val_matches = set(p0_val + np_val)
    test_matches = set(p0_test + np_test)

    # Build split datasets
    splits = {"train": [], "val": [], "test": []}
    for m in matches:
        if m in train_matches:
            splits["train"].extend(by_match[m])
        elif m in val_matches:
            splits["val"].extend(by_match[m])
        else:
            splits["test"].extend(by_match[m])

    # Shuffle within each split
    for s in splits.values():
        random.shuffle(s)

    # Write output
    args.output_dir.mkdir(parents=True, exist_ok=True)
    for split_name, data in splits.items():
        out_path = args.output_dir / f"599_event_conversations_{split_name}.jsonl"
        with open(out_path, "w", encoding="utf-8") as f:
            for r in data:
                f.write(json.dumps(r, ensure_ascii=False) + "\n")

    # Print stats
    print(f"\n{'='*60}")
    print(f"Dataset Split Summary")
    print(f"{'='*60}")
    for split_name, data in splits.items():
        match_count = len(set(r["metadata"]["match"] for r in data))
        labels = Counter(r["metadata"]["event_label"] for r in data)
        priorities = Counter(r["metadata"]["priority"] for r in data)
        print(f"\n  {split_name}:")
        print(f"    Records: {len(data)}, Matches: {match_count}")
        print(f"    Labels: {dict(labels)}")
        print(f"    Priorities: {dict(priorities)}")

    # Write split manifest
    manifest = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source": str(args.input),
        "total_records": len(records),
        "total_matches": len(matches),
        "splits": {},
    }
    for split_name, data in splits.items():
        match_names = sorted(set(r["metadata"]["match"] for r in data))
        manifest["splits"][split_name] = {
            "records": len(data),
            "matches": len(match_names),
            "match_names": match_names,
            "label_distribution": dict(Counter(r["metadata"]["event_label"] for r in data)),
        }

    manifest_path = args.output_dir / "split_manifest.json"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2))
    print(f"\nManifest: {manifest_path}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
