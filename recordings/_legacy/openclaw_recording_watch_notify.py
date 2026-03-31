#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import subprocess
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from zoneinfo import ZoneInfo

from notify_recording_summary import send_text_with_optional_media
from openclaw_recording_watch import (
    WATCH_RUNTIME_DIR,
    now_iso,
    run_watch_cycle,
)
from run_auto_capture import SessionLogger
from openclaw_recording_status import load_json


MEDIA_ROOT = Path("/tmp/openclaw/recording_watch")
MAX_DISPLAY_COUNT = 4
LA_TZ = ZoneInfo("America/Los_Angeles")
BJ_TZ = ZoneInfo("Asia/Shanghai")


def capture_desktop_screenshots(job_id: str) -> list[Path]:
    media_dir = MEDIA_ROOT / job_id
    media_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    screenshots: list[Path] = []

    for display_idx in range(1, MAX_DISPLAY_COUNT + 1):
        screenshot_path = media_dir / f"watch_{stamp}_display{display_idx}.jpg"
        try:
            completed = subprocess.run(
                ["/usr/sbin/screencapture", "-t", "jpg", f"-D{display_idx}", "-x", str(screenshot_path)],
                check=False,
                capture_output=True,
                text=True,
            )
        except Exception:
            break
        if completed.returncode != 0 or not screenshot_path.exists():
            if display_idx == 1:
                break
            break
        screenshots.append(screenshot_path)

    if screenshots:
        return screenshots

    fallback = media_dir / f"watch_{stamp}.jpg"
    try:
        completed = subprocess.run(
            ["/usr/sbin/screencapture", "-t", "jpg", "-x", str(fallback)],
            check=False,
            capture_output=True,
            text=True,
        )
    except Exception:
        return []
    if completed.returncode == 0 and fallback.exists():
        return [fallback]
    return []


def read_log_lines(path: Path) -> list[str]:
    if not path.exists():
        return []
    try:
        return path.read_text(encoding="utf-8").splitlines()
    except Exception:
        return []


def pick_explanation(log_lines: list[str]) -> str:
    if not log_lines:
        return ""
    preferred_markers = [
        "距离开赛约",
        "当前没有可直接打开的直播链接",
        "无直播链接",
        "跳过本轮",
        "未给比赛绑定到数据源",
        "已触发目标录制",
        "释放已完成锁",
    ]
    for line in reversed(log_lines):
        text = line.split("] ", 1)[-1].strip()
        if any(marker in text for marker in preferred_markers):
            return text
    return log_lines[-1].split("] ", 1)[-1].strip()


def format_checked_at(checked_at: str) -> tuple[str, str]:
    try:
        dt = datetime.fromisoformat(checked_at)
    except Exception:
        dt = datetime.now()
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=LA_TZ)
    la = dt.astimezone(LA_TZ)
    bj = dt.astimezone(BJ_TZ)
    return la.strftime("%Y-%m-%d %H:%M"), bj.strftime("%H:%M")


def summarize_trigger_mode(payload: dict) -> str:
    launched = payload.get("launched") or []
    if not launched:
        return "不适用（本轮无新录制启动）"
    if any(item.get("trigger_mode") == "test_only" for item in launched):
        return "包含 test_only_no_data_binding"
    if any(item.get("trigger_mode") == "mixed" for item in launched):
        return "包含 mixed（部分无数据测试流）"
    return "无，本轮新启动录制均已绑定数据"


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


def count_launched_streams(launched: list[dict]) -> int:
    total = 0
    for item in launched or []:
        selected = item.get("selected") or []
        if selected:
            total += len(selected)
        else:
            signatures = item.get("match_signatures") or []
            total += max(1, len(signatures))
    return total


def build_watch_message(payload: dict, explanation: str) -> str:
    la_text, bj_text = format_checked_at(payload.get("checked_at") or now_iso())
    launched = payload.get("launched") or []
    active_locks = payload.get("active_locks") or {}
    session_dir = launched[0].get("session_dir") if launched else ""
    discovered = int(payload.get("discovered_matches") or 0)
    active_sessions = len(active_locks)
    active_streams = count_active_streams(active_locks)
    launched_streams = count_launched_streams(launched)

    lines = [
        "巡检结果摘要",
        f"- 巡检时间：{la_text}（America/Los_Angeles） / 北京时间 {bj_text}",
        f"- 发现直播数：{discovered} 场",
        f"- 新启动会话数：{len(launched)}",
        f"- 新启动录制路数：{launched_streams}",
        f"- 当前活跃 session 数：{active_sessions}",
        f"- 当前活跃录制总路数：{active_streams}",
        f"- 启动录制 session_dir：{session_dir or '无（本轮未启动任何录制）'}",
        f"- test_only_no_data_binding：{summarize_trigger_mode(payload)}",
    ]
    if explanation:
        lines.append(f"说明：{explanation}")
    return "\n".join(lines)


def watch_state_file(job_id: str) -> Path:
    return WATCH_RUNTIME_DIR / f"{job_id}.json"


def should_send_notification(job_id: str, every_minutes: int) -> bool:
    if every_minutes <= 0:
        return True
    state = load_json(watch_state_file(job_id)) or {}
    last_sent_at = state.get("last_watch_notify_at", "")
    if not last_sent_at:
        return True
    try:
        last_dt = datetime.fromisoformat(last_sent_at)
    except Exception:
        return True
    if last_dt.tzinfo is None:
        last_dt = last_dt.replace(tzinfo=LA_TZ)
    now_dt = datetime.now(LA_TZ)
    return (now_dt - last_dt).total_seconds() >= every_minutes * 60


def record_notification(job_id: str, message: str, screenshot_paths: list[Path]) -> None:
    path = watch_state_file(job_id)
    payload = load_json(path) or {}
    payload["last_watch_notify_at"] = datetime.now(LA_TZ).isoformat()
    payload["last_watch_notify_message"] = message
    payload["last_watch_notify_screenshots"] = [str(path) for path in screenshot_paths]
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="Run one watch cycle and send a Feishu summary with screenshot.")
    parser.add_argument("--config", required=True)
    parser.add_argument("--job-id", required=True)
    parser.add_argument("--browser", default="safari")
    parser.add_argument("--output-root", required=True)
    parser.add_argument("--interval-minutes", type=int, default=10)
    parser.add_argument("--max-streams", type=int, default=4)
    parser.add_argument("--progress-interval-minutes", type=int, default=0)
    parser.add_argument("--channel", default="feishu")
    parser.add_argument("--target", required=True)
    parser.add_argument("--account", default="")
    parser.add_argument("--screenshot-scope", default="desktop")
    parser.add_argument("--notify-every-minutes", type=int, default=10)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    WATCH_RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
    log_path = WATCH_RUNTIME_DIR / f"{args.job_id}.log"
    before_lines = read_log_lines(log_path)
    logger = SessionLogger(str(log_path))

    watch_args = SimpleNamespace(
        config=args.config,
        job_id=args.job_id,
        browser=args.browser,
        output_root=args.output_root,
        interval_minutes=args.interval_minutes,
        check_once=True,
        loop=False,
        override_match_query=[],
        override_replace=False,
        gtypes="",
        duration_minutes=0,
        max_streams=args.max_streams,
        progress_interval_minutes=args.progress_interval_minutes,
        stop_at="",
        max_runtime_minutes=0,
        dry_run=args.dry_run,
    )

    payload = run_watch_cycle(watch_args, logger)
    after_lines = read_log_lines(log_path)
    new_lines = after_lines[len(before_lines):] if len(after_lines) >= len(before_lines) else after_lines
    explanation = pick_explanation(new_lines)
    message = build_watch_message(payload, explanation)

    screenshot_paths: list[Path] = []
    sent = should_send_notification(args.job_id, args.notify_every_minutes)
    rc = 0
    if sent:
        if args.screenshot_scope == "desktop":
            screenshot_paths = capture_desktop_screenshots(args.job_id)

        rc = send_text_with_optional_media(
            args.channel,
            args.target,
            message,
            account=(args.account or None),
            dry_run=args.dry_run,
            media=str(screenshot_paths[0]) if screenshot_paths else None,
        )
        if rc == 0 and len(screenshot_paths) > 1:
            for idx, screenshot_path in enumerate(screenshot_paths[1:], start=2):
                followup = f"巡检附图：显示器 {idx}"
                followup_rc = send_text_with_optional_media(
                    args.channel,
                    args.target,
                    followup,
                    account=(args.account or None),
                    dry_run=args.dry_run,
                    media=str(screenshot_path),
                )
                if followup_rc != 0:
                    logger.log(f"巡检附图发送失败: 显示器 {idx} rc={followup_rc} path={screenshot_path}", "WARN")
                    rc = followup_rc
                    break
        if rc == 0 and not args.dry_run:
            record_notification(args.job_id, message, screenshot_paths)

    result = {
        "ok": rc == 0,
        "job_id": args.job_id,
        "checked_at": payload.get("checked_at"),
        "launched_count": len(payload.get("launched") or []),
        "discovered_matches": payload.get("discovered_matches", 0),
        "active_locks": len(payload.get("active_locks") or {}),
        "sent": sent,
        "notify_every_minutes": args.notify_every_minutes,
        "screenshot_paths": [str(path) for path in screenshot_paths],
        "message": message,
        "send_rc": rc,
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if rc == 0 else rc


if __name__ == "__main__":
    raise SystemExit(main())
