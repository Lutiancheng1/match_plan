#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import subprocess
from datetime import datetime
from pathlib import Path


PROJECT_DIR = Path("/Users/niannianshunjing/match_plan/recordings")
RUN_SCRIPT = PROJECT_DIR / "run_auto_capture.py"
PROGRESS_SCRIPT = PROJECT_DIR / "openclaw_recording_progress.py"
DEFAULT_OUTPUT_ROOT = Path("/Volumes/990 PRO PCIe 4T/match_plan_recordings")
DEFAULT_NOTIFY_CHANNEL = "feishu"
DEFAULT_NOTIFY_ACCOUNT = "legacy"
DEFAULT_NOTIFY_TARGET = "oc_d8caa357cf6943f7a0b2917a2488876a"
LATEST_JOB_PATH = PROJECT_DIR / "last_openclaw_recording.json"


def build_session_dir(root: Path, session_id: str) -> Path:
    date_folder = datetime.now().strftime("%Y-%m-%d")
    return root / date_folder / f"session_{session_id}"


def build_run_command(args: argparse.Namespace, session_id: str) -> list[str]:
    segment_minutes = args.segment_minutes or min(max(args.duration_minutes, 1), 30)
    cmd = [
        "python3",
        str(RUN_SCRIPT),
        "--browser",
        args.browser,
        "--max-streams",
        str(args.max_streams),
        "--segment-minutes",
        str(segment_minutes),
        "--max-duration-minutes",
        str(args.duration_minutes),
        "--session-id",
        session_id,
    ]
    if args.selected_matches_file:
        cmd.extend(["--selected-matches-file", args.selected_matches_file])
    if args.match_query:
        cmd.extend(["--match-query", args.match_query])
    if args.all:
        cmd.append("--all")
    elif args.gtypes:
        cmd.extend(["--gtypes", args.gtypes])
    if args.watch_job_id:
        cmd.extend(["--watch-job-id", args.watch_job_id])
    if args.trigger_reason:
        cmd.extend(["--trigger-reason", args.trigger_reason])
    if args.match_rule_source:
        cmd.extend(["--match-rule-source", args.match_rule_source])
    if args.trigger_mode:
        cmd.extend(["--trigger-mode", args.trigger_mode])
    if args.watch_lock_key:
        cmd.extend(["--watch-lock-key", args.watch_lock_key])
    if not args.no_notify:
        cmd.extend(["--notify-channel", args.notify_channel])
        cmd.extend(["--notify-target", args.notify_target])
        cmd.extend(["--notify-title", args.notify_title])
        if args.notify_account:
            cmd.extend(["--notify-account", args.notify_account])
        if args.progress_interval_minutes > 0 and not args.keep_inline_notify:
            cmd.append("--disable-final-notify")
    return cmd


def build_progress_command(args: argparse.Namespace, session_dir: Path) -> list[str]:
    return [
        "python3",
        str(PROGRESS_SCRIPT),
        "--session-dir",
        str(session_dir),
        "--interval-minutes",
        str(args.progress_interval_minutes),
        "--channel",
        args.notify_channel,
        "--target",
        args.notify_target,
        "--account",
        args.notify_account,
        "--title",
        args.notify_title,
        "--screenshot-scope",
        args.progress_screenshot_scope,
    ]


def write_launch_record(session_dir: Path, payload: dict) -> None:
    session_dir.mkdir(parents=True, exist_ok=True)
    (session_dir / "openclaw_launch.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2))
    LATEST_JOB_PATH.write_text(json.dumps(payload, ensure_ascii=False, indent=2))


def launch_background_process(cmd: list[str], cwd: Path, log_path: Path, extra_env: dict[str, str] | None = None) -> int:
    env = os.environ.copy()
    if extra_env:
        env.update(extra_env)
    with log_path.open("a", encoding="utf-8") as log_file:
        proc = subprocess.Popen(
            cmd,
            cwd=str(cwd),
            env=env,
            stdout=log_file,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
    return proc.pid


def main() -> int:
    parser = argparse.ArgumentParser(description="Launch the match_plan recording pipeline for OpenClaw.")
    parser.add_argument("--duration-minutes", type=int, required=True)
    parser.add_argument("--max-streams", type=int, default=4)
    parser.add_argument("--gtypes", default="FT")
    parser.add_argument("--all", action="store_true")
    parser.add_argument("--match-query", default="")
    parser.add_argument("--selected-matches-file", default="")
    parser.add_argument("--browser", choices=["chrome", "safari"], default="safari")
    parser.add_argument("--segment-minutes", type=int, default=0)
    parser.add_argument("--output-root", default=str(DEFAULT_OUTPUT_ROOT))
    parser.add_argument("--session-id", default="")
    parser.add_argument("--watch-job-id", default="")
    parser.add_argument("--trigger-reason", default="")
    parser.add_argument("--match-rule-source", default="")
    parser.add_argument("--trigger-mode", default="")
    parser.add_argument("--watch-lock-key", default="")
    parser.add_argument("--progress-interval-minutes", type=int, default=0)
    parser.add_argument("--progress-screenshot-scope", default="desktop")
    parser.add_argument("--keep-inline-notify", action="store_true")
    parser.add_argument("--notify-channel", default=DEFAULT_NOTIFY_CHANNEL)
    parser.add_argument("--notify-account", default=DEFAULT_NOTIFY_ACCOUNT)
    parser.add_argument("--notify-target", default=DEFAULT_NOTIFY_TARGET)
    parser.add_argument("--notify-timeout-seconds", type=int, default=7200)
    parser.add_argument("--notify-title", default="录制任务已结束。")
    parser.add_argument("--no-notify", action="store_true")
    parser.add_argument("--foreground", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    session_id = args.session_id.strip() or datetime.now().strftime("%Y%m%d_%H%M%S")
    output_root = Path(args.output_root)
    session_dir = build_session_dir(output_root, session_id)
    run_cmd = build_run_command(args, session_id)
    use_inline_notify = (not args.no_notify) and not (
        args.progress_interval_minutes > 0 and not args.keep_inline_notify
    )

    payload = {
        "session_id": session_id,
        "session_dir": str(session_dir),
        "started_at": datetime.now().isoformat(),
        "project_dir": str(PROJECT_DIR),
        "run_command": run_cmd,
        "notify_mode": "inline" if use_inline_notify else ("progress_only" if args.progress_interval_minutes > 0 else "disabled"),
        "notify_config": {} if not (use_inline_notify or args.progress_interval_minutes > 0) else {
            "channel": args.notify_channel,
            "account": args.notify_account,
            "target": args.notify_target,
            "title": args.notify_title,
            "timeout_seconds": args.notify_timeout_seconds,
        },
        "progress": {
            "enabled": args.progress_interval_minutes > 0,
            "interval_minutes": args.progress_interval_minutes,
            "screenshot_scope": args.progress_screenshot_scope,
            "inline_notify_kept": bool(args.keep_inline_notify),
        },
        "max_streams": args.max_streams,
        "gtypes": None if args.all else args.gtypes,
        "all": args.all,
        "match_query": args.match_query,
        "selected_matches_file": args.selected_matches_file,
        "browser": args.browser,
        "duration_minutes": args.duration_minutes,
        "segment_minutes": args.segment_minutes or min(max(args.duration_minutes, 1), 30),
        "watch": {
            "watch_job_id": args.watch_job_id,
            "trigger_reason": args.trigger_reason,
            "target_match_rule_source": args.match_rule_source,
            "trigger_mode": args.trigger_mode,
            "session_lock_metadata": {
                "watch_lock_key": args.watch_lock_key,
            },
        },
    }

    if args.dry_run:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0

    write_launch_record(session_dir, payload)
    launch_log = session_dir / "openclaw_process.log"
    progress_cmd = []
    if args.progress_interval_minutes > 0 and args.notify_target:
        progress_cmd = build_progress_command(args, session_dir)
        payload["progress"]["command"] = progress_cmd

    if args.foreground:
        progress_pid = None
        if progress_cmd:
            progress_pid = launch_background_process(progress_cmd, PROJECT_DIR, launch_log)
            payload["progress_pid"] = progress_pid
            write_launch_record(session_dir, payload)
        env = os.environ.copy()
        env["MATCH_RECORDING_SESSION_ID"] = session_id
        completed = subprocess.run(run_cmd, cwd=str(PROJECT_DIR), env=env)
        return completed.returncode

    run_pid = launch_background_process(
        run_cmd,
        PROJECT_DIR,
        launch_log,
        extra_env={"MATCH_RECORDING_SESSION_ID": session_id},
    )
    payload["run_pid"] = run_pid
    if progress_cmd:
        progress_pid = launch_background_process(progress_cmd, PROJECT_DIR, launch_log)
        payload["progress_pid"] = progress_pid

    write_launch_record(session_dir, payload)
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
