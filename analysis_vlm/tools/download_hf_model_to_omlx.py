#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path
from urllib.parse import quote

import requests


DEFAULT_ENDPOINT = "https://hf-mirror.com"
DEFAULT_MODEL_ROOT = Path("/Users/niannianshunjing/.omlx/models")
DEFAULT_EXCLUDES = [
    "examples/",
    "*.png",
    "*.jpg",
    "*.jpeg",
    "*.mp4",
    "*.webm",
    "*.gif",
]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Download a Hugging Face model repo into the local OMLX model directory."
    )
    parser.add_argument("repo_id", help="Hugging Face repo id, e.g. openbmb/MiniCPM-V-4_5-int4")
    parser.add_argument(
        "--target-name",
        help="Local directory name under the OMLX models folder. Defaults to the repo basename.",
    )
    parser.add_argument(
        "--endpoint",
        default=DEFAULT_ENDPOINT,
        help=f"Hub endpoint, defaults to {DEFAULT_ENDPOINT}",
    )
    parser.add_argument(
        "--models-root",
        default=str(DEFAULT_MODEL_ROOT),
        help=f"OMLX model root, defaults to {DEFAULT_MODEL_ROOT}",
    )
    parser.add_argument(
        "--exclude",
        action="append",
        default=[],
        help="Extra file glob/prefix to skip. Can be repeated.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="List files and target directory without downloading.",
    )
    return parser.parse_args()


def should_skip(path: str, patterns: list[str]) -> bool:
    for pattern in patterns:
        if pattern.endswith("/"):
            if path.startswith(pattern):
                return True
            continue
        if Path(path).match(pattern):
            return True
    return False


def fetch_repo_files(endpoint: str, repo_id: str) -> list[str]:
    api_url = f"{endpoint.rstrip('/')}/api/models/{repo_id}"
    response = requests.get(api_url, timeout=30)
    response.raise_for_status()
    payload = response.json()
    return [item["rfilename"] for item in payload.get("siblings", [])]


def curl_download(url: str, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    partial = destination.with_suffix(destination.suffix + ".part")
    cmd = [
        "curl",
        "-L",
        "--fail",
        "--retry",
        "5",
        "--retry-delay",
        "2",
        "-C",
        "-",
        "-o",
        str(partial),
        url,
    ]
    completed = subprocess.run(cmd, check=False, capture_output=True, text=True)
    if completed.returncode != 0:
        raise RuntimeError(completed.stderr.strip() or completed.stdout.strip() or f"curl failed: {url}")
    partial.replace(destination)


def main() -> int:
    args = parse_args()
    repo_id = args.repo_id
    target_name = args.target_name or repo_id.rsplit("/", 1)[-1]
    endpoint = args.endpoint.rstrip("/")
    models_root = Path(args.models_root).expanduser()
    target_dir = models_root / target_name
    excludes = DEFAULT_EXCLUDES + list(args.exclude)

    try:
        repo_files = fetch_repo_files(endpoint, repo_id)
    except Exception as exc:
        print(f"[ERROR] Failed to fetch repo metadata for {repo_id}: {exc}", file=sys.stderr)
        return 1

    kept_files = [path for path in repo_files if not should_skip(path, excludes)]
    skipped_files = [path for path in repo_files if should_skip(path, excludes)]

    print(f"repo: {repo_id}")
    print(f"target_dir: {target_dir}")
    print(f"total_files: {len(repo_files)} kept: {len(kept_files)} skipped: {len(skipped_files)}")

    if args.dry_run:
        for path in kept_files:
            print(path)
        return 0

    target_dir.mkdir(parents=True, exist_ok=True)

    manifest = {
        "repo_id": repo_id,
        "endpoint": endpoint,
        "target_dir": str(target_dir),
        "kept_files": kept_files,
        "skipped_files": skipped_files,
    }
    (target_dir / ".download_manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    failures: list[dict[str, str]] = []
    downloaded = 0
    skipped_existing = 0
    for index, path in enumerate(kept_files, start=1):
        destination = target_dir / path
        if destination.exists() and destination.stat().st_size > 0:
            skipped_existing += 1
            print(f"[{index}/{len(kept_files)}] skip existing {path}")
            continue
        url = f"{endpoint}/{repo_id}/resolve/main/{quote(path)}"
        print(f"[{index}/{len(kept_files)}] download {path}")
        try:
            curl_download(url, destination)
            downloaded += 1
        except Exception as exc:
            failures.append({"file": path, "error": str(exc)})
            print(f"[WARN] failed {path}: {exc}", file=sys.stderr)

    result = {
        "repo_id": repo_id,
        "target_dir": str(target_dir),
        "downloaded": downloaded,
        "skipped_existing": skipped_existing,
        "failed": failures,
    }
    (target_dir / ".download_result.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if not failures else 2


if __name__ == "__main__":
    raise SystemExit(main())
