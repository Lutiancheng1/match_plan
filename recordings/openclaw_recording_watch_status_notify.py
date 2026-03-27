#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import subprocess
import time
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from notify_recording_summary import send_text_with_optional_media
from openclaw_recording_status import is_pid_alive, load_json
from openclaw_recording_watch import (
    WATCH_RUNTIME_DIR,
    reconcile_active_locks,
    watch_service_state_path,
    watch_state_path,
)
from run_auto_capture import SessionLogger


MEDIA_ROOT = Path("/tmp/openclaw/recording_watch")
MAX_DISPLAY_COUNT = 4
LA_TZ = ZoneInfo("America/Los_Angeles")
BJ_TZ = ZoneInfo("Asia/Shanghai")


def parse_iso_datetime(value: str) -> datetime | None:
    text = (value or "").strip()
    if not text:
        return None
    try:
        dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except Exception:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=LA_TZ)
    return dt


def capture_desktop_screenshots(job_id: str) -> list[Path]:
    media_dir = MEDIA_ROOT / job_id
    media_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    screenshots: list[Path] = []
    for display_idx in range(1, MAX_DISPLAY_COUNT + 1):
        screenshot_path = media_dir / f"watch_status_{stamp}_display{display_idx}.jpg"
        completed = subprocess.run(
            ["/usr/sbin/screencapture", "-t", "jpg", f"-D{display_idx}", "-x", str(screenshot_path)],
            check=False,
            capture_output=True,
            text=True,
        )
        if completed.returncode != 0 or not screenshot_path.exists():
            if display_idx == 1:
                break
            break
        screenshots.append(screenshot_path)
    if screenshots:
        return screenshots
    fallback = media_dir / f"watch_status_{stamp}.jpg"
    completed = subprocess.run(
        ["/usr/sbin/screencapture", "-t", "jpg", "-x", str(fallback)],
        check=False,
        capture_output=True,
        text=True,
    )
    if completed.returncode == 0 and fallback.exists():
        return [fallback]
    return []


def count_active_streams(active_locks: dict) -> int:
    total = 0
    for item in (active_locks or {}).values():
        selected = item.get("selected") or []
        if selected:
            total += len(selected)
        else:
            signatures = item.get("match_signatures") or []
            total += max(1, len(signatures))
    return total


def format_dt_pair(dt: datetime) -> tuple[str, str]:
    la = dt.astimezone(LA_TZ)
    bj = dt.astimezone(BJ_TZ)
    return la.strftime("%Y-%m-%d %H:%M"), bj.strftime("%Y-%m-%d %H:%M")


def build_status_message(job_id: str, state: dict) -> str:
    now_dt = datetime.now(LA_TZ)
    la_now, bj_now = format_dt_pair(now_dt)
    updated_at = state.get("updated_at", "")
    updated_text = updated_at or "unknown"
    active_locks = state.get("active_locks") or {}
    history = state.get("history") or []
    active_sessions = len(active_locks)
    active_streams = count_active_streams(active_locks)
    lines = [
        "watch 任务状态更新",
        f"- 任务ID：{job_id}",
        f"- 当前时间：{la_now}（America/Los_Angeles） / 北京时间 {bj_now}",
        f"- 状态更新时间：{updated_text}",
        f"- 当前活跃 session 数：{active_sessions}",
        f"- 当前活跃录制总路数：{active_streams}",
    ]
    if active_locks:
        lines.append("- 当前活跃录制：")
        for payload in active_locks.values():
            session_dir = payload.get("session_dir", "")
            selected = payload.get("selected") or []
            trigger_mode = payload.get("trigger_mode", "unknown")
            lines.append(f"  • {session_dir or 'unknown session'} | 模式:{trigger_mode} | 路数:{len(selected) or max(1, len(payload.get('match_signatures') or []))}")
            for name in selected[:8]:
                lines.append(f"    - {name}")
    else:
        lines.append("- 当前无活跃录制")
    if history:
        recent = history[-1]
        lines.append(
            f"- 最近已结束 session：{recent.get('session_dir') or 'unknown'} | started_at={recent.get('started_at','')} | released_at={recent.get('released_at','')}"
        )
    return "\n".join(lines)


def send_with_screenshots(
    channel: str,
    target: str,
    account: str,
    message: str,
    screenshots: list[Path],
    dry_run: bool,
) -> int:
    rc = send_text_with_optional_media(
        channel,
        target,
        message,
        account=account or None,
        dry_run=dry_run,
        media=str(screenshots[0]) if screenshots else None,
    )
    if rc != 0 or dry_run:
        return rc
    for idx, extra in enumerate(screenshots[1:], start=2):
        extra_message = f"巡检附图：显示器 {idx}"
        extra_rc = send_text_with_optional_media(
            channel,
            target,
            extra_message,
            account=account or None,
            dry_run=dry_run,
            media=str(extra),
        )
        if extra_rc != 0:
            rc = extra_rc
    return rc


def run_once(job_id: str, channel: str, target: str, account: str, screenshot_scope: str, dry_run: bool) -> int:
    WATCH_RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
    log_path = WATCH_RUNTIME_DIR / f"{job_id}.status_notify.log"
    logger = SessionLogger(str(log_path))
    try:
        state_path = watch_state_path(job_id)
        state = load_json(state_path) or {}
        if state:
            state = reconcile_active_locks(state, logger)
            state_path.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
        message = build_status_message(job_id, state)
        screenshots = capture_desktop_screenshots(job_id) if screenshot_scope == "desktop" else []
        return send_with_screenshots(channel, target, account, message, screenshots, dry_run)
    finally:
        logger.close()


def should_exit_loop(job_id: str) -> bool:
    state = load_json(watch_state_path(job_id)) or {}
    active_locks = state.get("active_locks") or {}
    if active_locks:
        return False

    now_dt = datetime.now(LA_TZ)
    stop_at = parse_iso_datetime(state.get("stop_at", ""))
    if stop_at and now_dt >= stop_at.astimezone(LA_TZ):
        return True

    service_state = load_json(watch_service_state_path(job_id)) or {}
    watch_pid = service_state.get("watch_pid")
    updated_at = parse_iso_datetime(service_state.get("updated_at", "")) or parse_iso_datetime(state.get("updated_at", ""))
    stopped_at = parse_iso_datetime(service_state.get("stopped_at", ""))
    interval_minutes = int(service_state.get("interval_minutes") or state.get("interval_minutes") or 0)
    stale_seconds = max(600, interval_minutes * 180) if interval_minutes > 0 else 900

    if isinstance(watch_pid, int) and watch_pid > 0 and not is_pid_alive(watch_pid):
        return True
    if stopped_at and now_dt >= stopped_at.astimezone(LA_TZ):
        return True
    if updated_at and (now_dt - updated_at.astimezone(LA_TZ)).total_seconds() > stale_seconds:
        return True
    return False


def main() -> int:
    parser = argparse.ArgumentParser(description="Send periodic watch status updates with screenshots.")
    parser.add_argument("--job-id", required=True)
    parser.add_argument("--interval-minutes", type=int, default=30)
    parser.add_argument("--channel", default="feishu")
    parser.add_argument("--target", required=True)
    parser.add_argument("--account", default="")
    parser.add_argument("--screenshot-scope", default="desktop")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--loop", action="store_true")
    args = parser.parse_args()

    if not args.loop:
        return run_once(args.job_id, args.channel, args.target, args.account, args.screenshot_scope, args.dry_run)

    sleep_seconds = max(60, int(args.interval_minutes * 60))
    while True:
        run_once(args.job_id, args.channel, args.target, args.account, args.screenshot_scope, args.dry_run)
        if should_exit_loop(args.job_id):
            break
        time.sleep(sleep_seconds)


if __name__ == "__main__":
    raise SystemExit(main())
