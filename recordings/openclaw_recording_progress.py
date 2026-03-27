#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import subprocess
import time
from datetime import datetime
from pathlib import Path

from notify_recording_summary import send_session_summary, send_text_with_optional_media
from openclaw_recording_status import get_session_status, load_json


PROJECT_DIR = Path("/Users/niannianshunjing/match_plan/recordings")
MEDIA_ROOT = Path("/tmp/openclaw/recording_progress")
WATCH_RUNTIME_NAME = "watch_runtime.json"


def now_iso() -> str:
    return datetime.now().isoformat()


def save_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def load_watch_runtime(session_dir: Path) -> dict:
    payload = load_json(session_dir / WATCH_RUNTIME_NAME)
    if payload:
        return payload
    launch = load_json(session_dir / "openclaw_launch.json") or {}
    watch = launch.get("watch") or {}
    payload = {
        "watch_job_id": watch.get("watch_job_id", ""),
        "trigger_reason": watch.get("trigger_reason", ""),
        "target_match_rule_source": watch.get("target_match_rule_source", ""),
        "trigger_mode": watch.get("trigger_mode", ""),
        "session_lock_metadata": (watch.get("session_lock_metadata") or {}),
        "progress_snapshots": [],
        "final_notify": {
            "sent": False,
            "at": "",
            "channel": "",
            "target": "",
            "account": "",
            "rc": None,
        },
    }
    save_json(session_dir / WATCH_RUNTIME_NAME, payload)
    return payload


def append_progress_snapshot(
    session_dir: Path,
    screenshot_path: Path | None,
    message: str,
    status_payload: dict,
) -> dict:
    runtime = load_watch_runtime(session_dir)
    runtime.setdefault("progress_snapshots", [])
    runtime["progress_snapshots"].append(
        {
            "sent_at": now_iso(),
            "screenshot_path": str(screenshot_path) if screenshot_path else "",
            "state": status_payload.get("state", "unknown"),
            "message": message,
        }
    )
    save_json(session_dir / WATCH_RUNTIME_NAME, runtime)
    return runtime


def record_final_notify(
    session_dir: Path,
    channel: str,
    target: str,
    account: str,
    rc: int,
) -> None:
    runtime = load_watch_runtime(session_dir)
    runtime["final_notify"] = {
        "sent": rc == 0,
        "at": now_iso(),
        "channel": channel,
        "target": target,
        "account": account,
        "rc": rc,
    }
    save_json(session_dir / WATCH_RUNTIME_NAME, runtime)


def merge_watch_runtime_into_result(session_dir: Path) -> None:
    result_path = session_dir / "session_result.json"
    runtime_path = session_dir / WATCH_RUNTIME_NAME
    if not result_path.exists() or not runtime_path.exists():
        return
    try:
        result = json.loads(result_path.read_text(encoding="utf-8"))
        runtime = json.loads(runtime_path.read_text(encoding="utf-8"))
    except Exception:
        return
    result["progress"] = {
        "progress_snapshots": runtime.get("progress_snapshots", []),
        "final_notify": runtime.get("final_notify", {}),
    }
    watch = result.get("watch") or {}
    watch.setdefault("watch_job_id", runtime.get("watch_job_id", ""))
    watch.setdefault("trigger_reason", runtime.get("trigger_reason", ""))
    watch.setdefault("target_match_rule_source", runtime.get("target_match_rule_source", ""))
    watch.setdefault("trigger_mode", runtime.get("trigger_mode", ""))
    merged_lock_metadata = dict(runtime.get("session_lock_metadata", {}) or {})
    merged_lock_metadata.update(watch.get("session_lock_metadata") or {})
    watch["session_lock_metadata"] = merged_lock_metadata
    result["watch"] = watch
    save_json(result_path, result)


def format_duration_from_launch(launch: dict) -> str:
    started_at = launch.get("started_at", "")
    if not started_at:
        return "unknown"
    try:
        start_dt = datetime.fromisoformat(started_at)
    except Exception:
        return "unknown"
    total = max(0, int((datetime.now() - start_dt).total_seconds()))
    return f"{total // 3600:02d}:{(total % 3600) // 60:02d}:{total % 60:02d}"


def capture_desktop_screenshot(session_dir: Path, session_id: str) -> Path | None:
    media_dir = MEDIA_ROOT / session_id
    media_dir.mkdir(parents=True, exist_ok=True)
    screenshot_path = media_dir / f"progress_{datetime.now().strftime('%Y%m%d_%H%M%S')}.png"
    try:
        completed = subprocess.run(
            ["/usr/sbin/screencapture", "-x", str(screenshot_path)],
            check=False,
            capture_output=True,
            text=True,
        )
    except Exception:
        return None
    if completed.returncode != 0 or not screenshot_path.exists():
        return None
    return screenshot_path


def build_running_message(session_dir: Path, status_payload: dict, launch: dict) -> str:
    selected_matches = status_payload.get("selected_matches") or []
    if isinstance(selected_matches, dict):
        selected_matches = selected_matches.get("selected_matches") or selected_matches.get("matches") or []
    if not isinstance(selected_matches, list):
        selected_matches = []
    watch = status_payload.get("watch") or {}
    stream_rows = []
    for item in selected_matches[:8]:
        name = " vs ".join(part for part in [item.get("team_h", ""), item.get("team_c", "")] if part) or item.get("league", "unknown")
        binding = item.get("data_binding_status", "unknown")
        note = item.get("recording_note", "")
        line = f"{name} | 数据:{binding}"
        if note:
            line += f" | {note}"
        stream_rows.append(line)

    lines = [
        "录制任务进度更新",
        f"目录：{session_dir}",
        f"状态：{status_payload.get('state', 'unknown')}",
        f"已录制：{format_duration_from_launch(launch)}",
        f"watch任务：{watch.get('watch_job_id') or 'unknown'} | 模式：{watch.get('trigger_mode') or 'unknown'}",
    ]
    if stream_rows:
        lines.append("当前目标：")
        lines.extend(stream_rows)
    log_tail = status_payload.get("recording_log_tail") or []
    if log_tail:
        lines.append(f"最近日志：{log_tail[-1]}")
    return "\n".join(lines)


def build_unknown_termination_message(session_dir: Path, status_payload: dict, launch: dict, title: str) -> str:
    lines = [
        title or "录制异常结束",
        f"目录：{session_dir}",
        "录制主进程已退出，但未生成 session_result.json。",
    ]
    watch = status_payload.get("watch") or {}
    if watch:
        lines.append(
            f"watch任务：{watch.get('watch_job_id') or 'unknown'} | 模式：{watch.get('trigger_mode') or 'unknown'}"
        )
    log_tail = status_payload.get("recording_log_tail") or []
    if log_tail:
        lines.append(f"最近日志：{log_tail[-1]}")
    return "\n".join(lines)


def send_progress_update(
    session_dir: Path,
    channel: str,
    target: str,
    account: str,
    screenshot_scope: str,
    dry_run: bool,
) -> int:
    launch = load_json(session_dir / "openclaw_launch.json") or {}
    status_payload = get_session_status(session_dir, launch=launch)
    message = build_running_message(session_dir, status_payload, launch)
    screenshot_path = None
    if screenshot_scope == "desktop":
        screenshot_path = capture_desktop_screenshot(session_dir, launch.get("session_id", "unknown"))
    rc = send_text_with_optional_media(
        channel,
        target,
        message,
        account=account or None,
        dry_run=dry_run,
        media=str(screenshot_path) if screenshot_path else None,
    )
    if not dry_run:
        append_progress_snapshot(session_dir, screenshot_path, message, status_payload)
    return rc


def main() -> int:
    parser = argparse.ArgumentParser(description="Send periodic recording progress updates with screenshot snapshots.")
    parser.add_argument("--session-dir", required=True)
    parser.add_argument("--interval-minutes", type=int, default=30)
    parser.add_argument("--channel", default="feishu")
    parser.add_argument("--target", required=True)
    parser.add_argument("--account", default="")
    parser.add_argument("--title", default="录制任务已结束。")
    parser.add_argument("--screenshot-scope", default="desktop")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    session_dir = Path(args.session_dir)
    if not session_dir.exists():
        raise SystemExit(f"session_dir 不存在: {session_dir}")

    launch = load_json(session_dir / "openclaw_launch.json") or {}
    if args.interval_minutes <= 0:
        args.interval_minutes = 30
    deadline = time.time() + (args.interval_minutes * 60)

    while True:
        status_payload = get_session_status(session_dir, launch=launch)
        state = status_payload.get("state")
        if state == "completed":
            rc = send_session_summary(
                session_dir,
                channel=args.channel,
                target=args.target,
                account=args.account or None,
                timeout_seconds=0,
                title=args.title,
                dry_run=args.dry_run,
            )
            if not args.dry_run:
                record_final_notify(session_dir, args.channel, args.target, args.account, rc)
                merge_watch_runtime_into_result(session_dir)
            return rc
        if state == "unknown":
            message = build_unknown_termination_message(
                session_dir,
                status_payload,
                launch,
                "录制异常结束",
            )
            rc = send_text_with_optional_media(
                args.channel,
                args.target,
                message,
                account=args.account or None,
                dry_run=args.dry_run,
            )
            if not args.dry_run:
                record_final_notify(session_dir, args.channel, args.target, args.account, rc)
            return rc

        now_ts = time.time()
        if now_ts >= deadline:
            send_progress_update(
                session_dir,
                channel=args.channel,
                target=args.target,
                account=args.account,
                screenshot_scope=args.screenshot_scope,
                dry_run=args.dry_run,
            )
            deadline = now_ts + (args.interval_minutes * 60)
        time.sleep(15)


if __name__ == "__main__":
    raise SystemExit(main())
