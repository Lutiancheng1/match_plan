#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path


def format_mmss(total_seconds: object) -> str:
    if not isinstance(total_seconds, (int, float)):
        return "unknown"
    total = int(round(float(total_seconds)))
    return f"{total // 60:02d}:{total % 60:02d}"


def load_result_when_ready(session_dir: Path, timeout_seconds: int) -> tuple[Path, dict] | tuple[None, None]:
    result_path = session_dir / "session_result.json"
    deadline = time.time() + timeout_seconds
    while time.time() < deadline:
        if result_path.exists():
            try:
                return result_path, json.loads(result_path.read_text())
            except json.JSONDecodeError:
                # Result file may still be flushing; wait briefly.
                time.sleep(2)
                continue
        time.sleep(15)
    return None, None


def load_result_now(session_dir: Path) -> tuple[Path, dict] | tuple[None, None]:
    result_path = session_dir / "session_result.json"
    if not result_path.exists():
        return None, None
    try:
        return result_path, json.loads(result_path.read_text())
    except json.JSONDecodeError:
        return None, None


def build_summary(session_dir: Path, payload: dict | None) -> str:
    if not payload:
        return f"录制结果文件未在等待时间内生成。\n目录：{session_dir}"

    streams = payload.get("streams") or payload.get("results") or []
    completed = sum(
        1 for stream in streams if str(stream.get("status", "")).lower() in {"completed", "ok", "success"}
    )
    lines = [f"目录：{session_dir}", f"共{len(streams)}路，完成{completed}路"]

    for stream in streams[:12]:
        name = (
            stream.get("match_display")
            or stream.get("match")
            or stream.get("title")
            or stream.get("match_id")
            or "unknown"
        )
        duration = format_mmss(stream.get("total_duration_sec"))
        binding = stream.get("data_binding_status") or "unknown"
        matched_rows = stream.get("matched_rows")
        note = stream.get("recording_note") or ""
        line = f"{name} | 时长:{duration} | 数据:{binding} | 匹配:{matched_rows}"
        if note:
            line += f" | {note}"
        lines.append(line)

    return "\n".join(lines)


def build_message(session_dir: Path, payload: dict | None, title: str) -> str:
    return f"{title}\n{build_summary(session_dir, payload)}"


def send_via_openclaw(
    channel: str,
    target: str,
    message: str,
    account: str | None = None,
    dry_run: bool = False,
    media: str | None = None,
) -> int:
    cmd = [
        "/opt/homebrew/bin/openclaw",
        "message",
        "send",
        "--channel",
        channel,
        "--target",
        target,
        "--message",
        message,
        "--json",
    ]
    if media:
        cmd.extend(["--media", media])
    if account:
        cmd.extend(["--account", account])
    if dry_run:
        cmd.append("--dry-run")

    completed = subprocess.run(cmd, capture_output=True, text=True)
    if completed.stdout:
        print(completed.stdout.strip())
    if completed.stderr:
        print(completed.stderr.strip(), file=sys.stderr)
    return completed.returncode


def send_text_with_optional_media(
    channel: str,
    target: str,
    message: str,
    account: str | None = None,
    dry_run: bool = False,
    media: str | None = None,
) -> int:
    return send_via_openclaw(
        channel,
        target,
        message,
        account=account,
        dry_run=dry_run,
        media=media,
    )


def send_session_summary(
    session_dir: Path,
    channel: str,
    target: str,
    account: str | None = None,
    timeout_seconds: int = 0,
    title: str = "录制任务已结束。",
    dry_run: bool = False,
) -> int:
    if timeout_seconds and timeout_seconds > 0:
        _, payload = load_result_when_ready(session_dir, timeout_seconds)
    else:
        _, payload = load_result_now(session_dir)
    message = build_message(session_dir, payload, title)
    return send_via_openclaw(channel, target, message, account=account, dry_run=dry_run)


def main() -> int:
    parser = argparse.ArgumentParser(description="Wait for a recording session result and send a summary via OpenClaw.")
    parser.add_argument("--session-dir", required=True, help="Session directory containing session_result.json")
    parser.add_argument("--channel", default="feishu", help="OpenClaw channel name")
    parser.add_argument("--target", required=True, help="OpenClaw target/group id")
    parser.add_argument("--account", help="Optional OpenClaw account id")
    parser.add_argument("--timeout-seconds", type=int, default=3600, help="How long to wait for session_result.json")
    parser.add_argument("--title", default="录制任务已结束。", help="Message title prefix")
    parser.add_argument("--dry-run", action="store_true", help="Build the message but do not actually send it")
    args = parser.parse_args()

    session_dir = Path(args.session_dir)
    return send_session_summary(
        session_dir,
        channel=args.channel,
        target=args.target,
        account=args.account,
        timeout_seconds=args.timeout_seconds,
        title=args.title,
        dry_run=args.dry_run,
    )


if __name__ == "__main__":
    raise SystemExit(main())
