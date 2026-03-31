#!/usr/bin/env python3
"""Merge LoRA adapter weights into the base model for deployment."""
from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

DEFAULT_MODEL = "/Users/niannianshunjing/.omlx/models/Qwen3.5-VL-9B-8bit-MLX-CRACK"
DEFAULT_ADAPTER_DIR = Path("/Users/niannianshunjing/match_plan/analysis_vlm/training/adapters")
DEFAULT_OUTPUT_DIR = Path("/Users/niannianshunjing/match_plan/analysis_vlm/training/merged_models")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Merge LoRA adapter into base model.")
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--adapter-path", type=Path, required=True, help="Path to adapter directory.")
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--dry-run", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    adapter_name = args.adapter_path.name
    output_path = args.output_dir / f"{adapter_name}_merged"
    output_path.mkdir(parents=True, exist_ok=True)

    cmd = [
        sys.executable, "-m", "mlx_lm.fuse",
        "--model", args.model,
        "--adapter-path", str(args.adapter_path),
        "--save-path", str(output_path),
    ]

    if args.dry_run:
        print("DRY RUN:")
        print(" ".join(cmd))
        return 0

    print(f"Merging adapter: {args.adapter_path}")
    print(f"Output: {output_path}")
    result = subprocess.run(cmd)

    if result.returncode == 0:
        print(f"\nMerged model saved to: {output_path}")
    else:
        print(f"\nMerge failed with exit code {result.returncode}")

    return result.returncode


if __name__ == "__main__":
    raise SystemExit(main())
