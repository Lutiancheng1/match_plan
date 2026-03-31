#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from openclaw_recording_launcher import (
    DEFAULT_NOTIFY_ACCOUNT,
    DEFAULT_NOTIFY_CHANNEL,
    DEFAULT_NOTIFY_TARGET,
    DEFAULT_OUTPUT_ROOT,
    PROJECT_DIR,
    build_session_dir,
)
from openclaw_recording_status import find_session_processes, is_pid_alive, load_json
from run_auto_capture import (
    ALL_GTYPES,
    DEFAULT_URL,
    SessionLogger,
    bind_selected_matches_to_feed,
    bootstrap_credentials,
    discover_live_matches,
    discover_live_matches_from_schedule,
    ensure_schedules_live_ready,
    fetch_dashboard,
    fetch_live_data_snapshot,
    filter_matches_ready_to_record,
    normalize_league_text,
    normalize_match_text,
)


DEFAULT_CONFIG_PATH = PROJECT_DIR / "watch_targets.json"
WATCH_RUNTIME_DIR = PROJECT_DIR / "watch_runtime"
LAUNCHER_PATH = PROJECT_DIR / "openclaw_recording_launcher.py"
DEFAULT_CHECK_INTERVAL_MINUTES = 10
DEFAULT_PROGRESS_INTERVAL_MINUTES = 30
LIVE_DASHBOARD_DIR = PROJECT_DIR.parent / "live_dashboard"
LIVE_DASHBOARD_ENV_PATH = LIVE_DASHBOARD_DIR / "live_dashboard.env"
LIVE_DASHBOARD_START_PATH = LIVE_DASHBOARD_DIR / "start_live_dashboard.sh"
LIVE_DASHBOARD_STOP_PATH = LIVE_DASHBOARD_DIR / "stop_live_dashboard.sh"
SERVICE_STATE_STALE_SECONDS = 900
DASHBOARD_RESTART_GRACE_SECONDS = 3


def now_iso() -> str:
    return datetime.now().isoformat()


def parse_stop_at(value: str) -> datetime | None:
    text = (value or "").strip()
    if not text:
        return None
    normalized = text.replace("T", " ")
    for fmt in ("%Y-%m-%d %H:%M", "%Y-%m-%d %H:%M:%S"):
        try:
            return datetime.strptime(normalized, fmt)
        except ValueError:
            continue
    raise ValueError(f"无法解析 stop-at 时间: {value}")


def resolve_deadline(args: argparse.Namespace) -> datetime | None:
    stop_at = parse_stop_at(getattr(args, "stop_at", ""))
    max_runtime_minutes = int(getattr(args, "max_runtime_minutes", 0) or 0)
    runtime_deadline = None
    if max_runtime_minutes > 0:
        runtime_deadline = datetime.now() + timedelta(minutes=max_runtime_minutes)
    if stop_at and runtime_deadline:
        return min(stop_at, runtime_deadline)
    return stop_at or runtime_deadline


def deadline_to_iso(deadline: datetime | None) -> str:
    return deadline.isoformat(timespec="seconds") if deadline else ""


def save_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def load_watch_config(path: Path) -> dict:
    if not path.exists():
        raise FileNotFoundError(f"watch 配置不存在: {path}")
    return json.loads(path.read_text(encoding="utf-8"))


def default_watch_state(job_id: str, config_path: Path, interval_minutes: int) -> dict:
    return {
        "job_id": job_id,
        "config_path": str(config_path),
        "interval_minutes": interval_minutes,
        "stop_at": "",
        "updated_at": now_iso(),
        "active_locks": {},
        "history": [],
    }


def watch_state_path(job_id: str) -> Path:
    return WATCH_RUNTIME_DIR / f"{job_id}.json"


def watch_service_state_path(job_id: str) -> Path:
    return WATCH_RUNTIME_DIR / f"{job_id}.service.json"


def load_watch_state(job_id: str, config_path: Path, interval_minutes: int) -> dict:
    path = watch_state_path(job_id)
    payload = load_json(path)
    if payload:
        return payload
    payload = default_watch_state(job_id, config_path, interval_minutes)
    save_json(path, payload)
    return payload


def save_watch_state(job_id: str, payload: dict) -> None:
    payload["updated_at"] = now_iso()
    save_json(watch_state_path(job_id), payload)


def parse_iso_datetime(value: str) -> datetime | None:
    text = (value or "").strip()
    if not text:
        return None
    try:
        dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except Exception:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def save_watch_service_state(args: argparse.Namespace, requested_gtypes: list[str]) -> None:
    payload = {
        "job_id": args.job_id,
        "watch_pid": os.getpid(),
        "config_path": str(args.config),
        "interval_minutes": int(getattr(args, "interval_minutes", 0) or 0),
        "requested_gtypes": [str(item).strip().lower() for item in (requested_gtypes or []) if str(item).strip()],
        "updated_at": now_iso(),
        "stopped_at": "",
    }
    save_json(watch_service_state_path(args.job_id), payload)


def mark_watch_service_stopped(job_id: str) -> None:
    path = watch_service_state_path(job_id)
    payload = load_json(path) or {"job_id": job_id}
    payload["updated_at"] = now_iso()
    payload["stopped_at"] = now_iso()
    save_json(path, payload)


def collect_active_watch_gtypes(current_job_id: str, current_gtypes: list[str]) -> tuple[list[str], list[str]]:
    union = {str(item).strip().lower() for item in (current_gtypes or []) if str(item).strip()}
    peer_jobs: list[str] = []
    for path in WATCH_RUNTIME_DIR.glob("*.service.json"):
        payload = load_json(path) or {}
        job_id = str(payload.get("job_id") or path.stem.replace(".service", ""))
        if not job_id or job_id == current_job_id:
            continue
        if payload.get("stopped_at"):
            continue
        updated_at = parse_iso_datetime(payload.get("updated_at", ""))
        if updated_at is None:
            continue
        interval_minutes = int(payload.get("interval_minutes") or 0)
        stale_seconds = max(SERVICE_STATE_STALE_SECONDS, interval_minutes * 180) if interval_minutes > 0 else SERVICE_STATE_STALE_SECONDS
        age_seconds = (datetime.now(timezone.utc) - updated_at.astimezone(timezone.utc)).total_seconds()
        if age_seconds > stale_seconds:
            continue
        peer_gtypes = [
            str(item).strip().lower()
            for item in (payload.get("requested_gtypes") or [])
            if str(item).strip()
        ]
        if not peer_gtypes:
            peer_gtypes = [str(item).strip().lower() for item in ALL_GTYPES]
        union.update(peer_gtypes)
        peer_jobs.append(job_id)
    return sorted(union), sorted(peer_jobs)


def build_target_defaults(config: dict) -> dict:
    defaults = config.get("defaults") or {}
    default_gtypes = defaults.get("gtypes", ["FT"])
    if default_gtypes is None:
        default_gtypes = ["FT"]
    elif isinstance(default_gtypes, str):
        default_gtypes = [g.strip().upper() for g in default_gtypes.split(",") if g.strip()]
    else:
        default_gtypes = [str(g).strip().upper() for g in default_gtypes if str(g).strip()]
    return {
        "browser": defaults.get("browser", "safari"),
        "gtypes": default_gtypes,
        "duration_minutes": int(defaults.get("duration_minutes", 30)),
        "max_streams": int(defaults.get("max_streams", 0) or 0),
        "check_interval_minutes": int(defaults.get("check_interval_minutes", DEFAULT_CHECK_INTERVAL_MINUTES)),
        "progress_interval_minutes": int(defaults.get("progress_interval_minutes", DEFAULT_PROGRESS_INTERVAL_MINUTES)),
        "allow_test_recording": bool(defaults.get("allow_test_recording", True)),
        "notify_channel": defaults.get("notify_channel", DEFAULT_NOTIFY_CHANNEL),
        "notify_account": defaults.get("notify_account", DEFAULT_NOTIFY_ACCOUNT),
        "notify_target": defaults.get("notify_target", DEFAULT_NOTIFY_TARGET),
    }


def resolve_targets(config: dict, args: argparse.Namespace) -> tuple[dict, list[dict]]:
    defaults = build_target_defaults(config)
    provided_flags = getattr(args, "_provided_flags", set())
    targets = []
    for idx, raw in enumerate(config.get("targets") or []):
        if not raw.get("enabled", True):
            continue
        if "gtypes" in raw:
            raw_gtypes = raw.get("gtypes")
            if isinstance(raw_gtypes, str):
                target_gtypes = [g.strip().upper() for g in raw_gtypes.split(",") if g.strip()]
            else:
                target_gtypes = [str(g).strip().upper() for g in (raw_gtypes or []) if str(g).strip()]
        else:
            target_gtypes = defaults["gtypes"]
        target = {
            "id": raw.get("id") or f"target_{idx+1}",
            "name": raw.get("name") or raw.get("match_query") or f"target_{idx+1}",
            "priority": int(raw.get("priority", 100)),
            "browser": raw.get("browser", defaults["browser"]),
            "gtypes": target_gtypes,
            "league_keywords": raw.get("league_keywords") or [],
            "team_keywords": raw.get("team_keywords") or [],
            "match_query": raw.get("match_query", ""),
            "allow_test_recording": bool(raw.get("allow_test_recording", defaults["allow_test_recording"])),
            "duration_minutes": int(raw.get("duration_minutes", defaults["duration_minutes"])),
            "max_streams": int(raw.get("max_streams", defaults["max_streams"]) or 0),
            "progress_interval_minutes": int(raw.get("progress_interval_minutes", defaults["progress_interval_minutes"])),
            "notify_channel": raw.get("notify_channel", defaults["notify_channel"]),
            "notify_account": raw.get("notify_account", defaults["notify_account"]),
            "notify_target": raw.get("notify_target", defaults["notify_target"]),
            "rule_source": "config",
        }
        targets.append(target)

    if args.override_match_query:
        queries = args.override_match_query
        if args.override_replace:
            targets = []
        for idx, query in enumerate(queries, start=1):
            if args.gtypes:
                override_gtypes = [g.strip().upper() for g in args.gtypes.split(",") if g.strip()]
            else:
                override_gtypes = defaults["gtypes"]
            duration_minutes = args.duration_minutes if "--duration-minutes" in provided_flags else defaults["duration_minutes"]
            max_streams = args.max_streams if "--max-streams" in provided_flags else defaults["max_streams"]
            progress_interval_minutes = (
                args.progress_interval_minutes
                if "--progress-interval-minutes" in provided_flags
                else defaults["progress_interval_minutes"]
            )
            targets.append(
                {
                    "id": f"override_{idx}",
                    "name": query,
                    "priority": 1000 + idx,
                    "browser": args.browser or defaults["browser"],
                    "gtypes": override_gtypes,
                    "league_keywords": [],
                    "team_keywords": [],
                    "match_query": query,
                    "allow_test_recording": defaults["allow_test_recording"],
                    "duration_minutes": int(duration_minutes),
                    "max_streams": int(max_streams),
                    "progress_interval_minutes": int(progress_interval_minutes),
                    "notify_channel": defaults["notify_channel"],
                    "notify_account": defaults["notify_account"],
                    "notify_target": defaults["notify_target"],
                    "rule_source": "message_override",
                }
            )
    targets.sort(key=lambda item: (-item["priority"], item["id"]))
    return defaults, targets


def resolve_watch_gtypes(targets: list[dict], defaults: dict) -> list[str]:
    resolved: set[str] = set()
    for target in targets:
        values = [str(g).strip().upper() for g in (target.get("gtypes") or []) if str(g).strip()]
        if not values:
            return list(ALL_GTYPES)
        resolved.update(values)
    if not resolved:
        values = [str(g).strip().upper() for g in (defaults.get("gtypes") or []) if str(g).strip()]
        if not values:
            return list(ALL_GTYPES)
        resolved.update(values)
    return sorted(resolved)


def _replace_env_assignment(lines: list[str], key: str, value: str) -> tuple[list[str], bool]:
    prefix = f"{key}="
    updated = []
    changed = False
    replaced = False
    for line in lines:
        if line.startswith(prefix):
            replaced = True
            new_line = f"{key}={value}"
            updated.append(new_line)
            if line != new_line:
                changed = True
        else:
            updated.append(line)
    if not replaced:
        updated.append(f"{key}={value}")
        changed = True
    return updated, changed


def wait_for_dashboard_scope(
    desired_gtypes: list[str],
    restart_started_at: datetime | None = None,
    timeout_seconds: int = 20,
) -> dict:
    expected = {str(item).strip().upper() for item in (desired_gtypes or []) if str(item).strip()}
    if not expected:
        expected = set(ALL_GTYPES)
    deadline = time.time() + timeout_seconds
    last_reason = "dashboard_unreachable"
    while time.time() < deadline:
        status_payload = fetch_dashboard("/api/status.json") or {}
        latest_payload = fetch_dashboard("/api/latest.json") or {}
        inputs = latest_payload.get("inputs") or {}
        current = {str(item).strip().upper() for item in (inputs.get("gtypes") or []) if str(item).strip()}
        if not current and latest_payload.get("feeds"):
            current = set(expected)
        last_success = parse_iso_datetime(status_payload.get("last_success", ""))
        snapshot_time = parse_iso_datetime(latest_payload.get("snapshot_time", ""))
        scope_ready = current == expected
        freshness_ready = True
        if restart_started_at:
            candidates = [item for item in (last_success, snapshot_time) if item is not None]
            freshness_ready = any(item.astimezone(timezone.utc) >= restart_started_at.astimezone(timezone.utc) for item in candidates)
        if scope_ready and freshness_ready:
            return {
                "ready": True,
                "reason": "scope_ready",
                "gtypes": sorted(current) or sorted(expected),
                "snapshot_time": latest_payload.get("snapshot_time", ""),
                "last_success": status_payload.get("last_success", ""),
            }
        if not scope_ready:
            last_reason = f"scope_mismatch:{','.join(sorted(current)) or 'empty'}"
        else:
            last_reason = "stale_snapshot"
        time.sleep(1)
    return {"ready": False, "reason": last_reason, "gtypes": sorted(expected)}


def sync_live_dashboard_scope(logger: SessionLogger, job_id: str, gtypes: list[str]) -> dict:
    desired_gtypes = [str(g).strip().lower() for g in (gtypes or ALL_GTYPES) if str(g).strip()]
    merged_gtypes, peer_jobs = collect_active_watch_gtypes(job_id, desired_gtypes)
    desired_text = ",".join(merged_gtypes)
    title = "全部比赛实时看板" if set(map(str.upper, merged_gtypes or [])) == set(ALL_GTYPES) else "实时比赛看板"

    if not LIVE_DASHBOARD_ENV_PATH.exists():
        logger.log(f"live_dashboard env 不存在，跳过球种同步: {LIVE_DASHBOARD_ENV_PATH}", "WARN")
        return {
            "changed": False,
            "restarted": False,
            "warmed": False,
            "gtypes": merged_gtypes,
            "peer_jobs": peer_jobs,
            "reason": "env_missing",
        }

    lines = LIVE_DASHBOARD_ENV_PATH.read_text(encoding="utf-8").splitlines()
    lines, changed_gtypes = _replace_env_assignment(lines, "GTYPES", desired_text)
    lines, changed_title = _replace_env_assignment(lines, "TITLE", title)
    changed = changed_gtypes or changed_title
    if changed:
        LIVE_DASHBOARD_ENV_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")
        logger.log(f"已同步 live_dashboard 球种范围: GTYPES={desired_text or 'all'}", "INFO")
    else:
        warm = wait_for_dashboard_scope(merged_gtypes, timeout_seconds=5)
        return {
            "changed": False,
            "restarted": False,
            "warmed": bool(warm.get("ready")),
            "warm_reason": warm.get("reason", ""),
            "gtypes": merged_gtypes,
            "peer_jobs": peer_jobs,
            "reason": "already_in_sync",
        }

    restarted = False
    restart_started_at = None
    if LIVE_DASHBOARD_START_PATH.exists():
        restart_started_at = datetime.now(timezone.utc)
        subprocess.run(["pkill", "-f", "serve_live_dashboard.py --host 127.0.0.1 --port 8765"], check=False)
        subprocess.run(["zsh", str(LIVE_DASHBOARD_STOP_PATH)], check=False, capture_output=True, text=True)
        started = subprocess.run(["zsh", str(LIVE_DASHBOARD_START_PATH)], check=False, capture_output=True, text=True)
        if started.returncode == 0:
            restarted = True
            logger.log("已重启 live_dashboard 以应用新的球种范围", "INFO")
            logger.log(f"等待 {DASHBOARD_RESTART_GRACE_SECONDS}s 让 live_dashboard 完成冷启动后再检测", "INFO")
            time.sleep(DASHBOARD_RESTART_GRACE_SECONDS)
        else:
            logger.log(f"重启 live_dashboard 失败: {started.stderr.strip() or started.stdout.strip()}", "WARN")
    warm = wait_for_dashboard_scope(merged_gtypes, restart_started_at=restart_started_at if restarted else None)
    if restarted and not warm.get("ready"):
        logger.log(f"live_dashboard 重启后未等到新范围快照: {warm.get('reason', 'unknown')}", "WARN")
    return {
        "changed": True,
        "restarted": restarted,
        "warmed": bool(warm.get("ready")),
        "warm_reason": warm.get("reason", ""),
        "gtypes": merged_gtypes,
        "peer_jobs": peer_jobs,
        "reason": "updated",
    }


def flatten_matches(grouped: dict) -> list[dict]:
    rows = []
    for matches in grouped.values():
        rows.extend(matches)
    return rows


def target_match_signature(match: dict) -> str:
    gid = match.get("gid", "")
    ecid = match.get("ecid", "")
    if gid or ecid:
        return f"{match.get('gtype','')}:gid:{gid or ecid}"
    league = normalize_league_text(match.get("league", ""))
    home = normalize_match_text(match.get("team_h", ""))
    away = normalize_match_text(match.get("team_c", ""))
    return f"{match.get('gtype','')}:{league}:{home}:{away}"


def match_target_rule(match: dict, target: dict) -> bool:
    gtypes = {g.strip().upper() for g in target.get("gtypes") or [] if g}
    if gtypes and (match.get("gtype", "").upper() not in gtypes):
        return False

    league_text = normalize_league_text(match.get("league", ""))
    teams_text = normalize_match_text(
        " ".join(part for part in [match.get("team_h", ""), match.get("team_c", "")] if part)
    )
    haystack = f"{teams_text} {league_text}".strip()

    league_keywords = [normalize_league_text(item) for item in target.get("league_keywords") or [] if item]
    if league_keywords and not any(keyword in league_text for keyword in league_keywords):
        return False

    team_keywords = [normalize_match_text(item) for item in target.get("team_keywords") or [] if item]
    if team_keywords and not any(keyword in haystack for keyword in team_keywords):
        return False

    query = normalize_match_text(target.get("match_query", ""))
    if query and query not in haystack:
        raw_query = target.get("match_query", "") or ""
        raw_parts = re.split(r"\s+(?:vs|v)\s+|\s+x\s+|,|/|\\|[-–—]", raw_query, flags=re.I)
        parts = [normalize_match_text(part) for part in raw_parts if normalize_match_text(part)]
        if parts:
            if not all(part in haystack for part in parts):
                return False
        else:
            fallback_parts = [part for part in query.split() if part]
            if not fallback_parts or not all(part in haystack for part in fallback_parts):
                return False

    return True


def discover_and_bind_matches(
    logger: SessionLogger,
    browser: str,
    target_gtypes: list[str],
    dashboard_scope: dict | None = None,
) -> tuple[list[dict], dict]:
    if not ensure_schedules_live_ready(logger, browser):
        return [], {}

    cookie, template, use_dashboard, feed_url, data_source = bootstrap_credentials(logger, browser)
    logger.log(f"watch巡检数据源: {data_source}")

    grouped = discover_live_matches_from_schedule(logger, browser)
    if not grouped:
        grouped = discover_live_matches(
            cookie,
            template,
            target_gtypes or ALL_GTYPES,
            logger,
            use_dashboard,
            feed_url=feed_url or DEFAULT_URL,
        )
    matches = flatten_matches(grouped)
    if not matches:
        return [], {
            "cookie": cookie,
            "template": template,
            "use_dashboard": use_dashboard,
            "feed_url": feed_url or DEFAULT_URL,
            "data_source": data_source,
        }

    context = {
        "cookie": cookie,
        "template": template,
        "use_dashboard": use_dashboard,
        "feed_url": feed_url or DEFAULT_URL,
        "data_source": data_source,
        "snapshot_rows_count": 0,
        "snapshot_healthy": False,
        "snapshot_reason": "",
    }
    try:
        snapshot_rows = fetch_live_data_snapshot(
            cookie,
            template,
            gtypes=target_gtypes or ALL_GTYPES,
            use_dashboard=use_dashboard,
            feed_url=feed_url or DEFAULT_URL,
        )
        context["snapshot_rows_count"] = len(snapshot_rows)
    except Exception as exc:
        logger.log(f"watch巡检抓取实时快照失败: {exc}", "WARN")
        if not use_dashboard:
            try:
                logger.log("直连快照失败，尝试回退到本地 dashboard 快照", "WARN")
                snapshot_rows = fetch_live_data_snapshot(
                    None,
                    None,
                    gtypes=target_gtypes or ALL_GTYPES,
                    use_dashboard=True,
                    feed_url=feed_url or DEFAULT_URL,
                )
                context["snapshot_rows_count"] = len(snapshot_rows)
                context["use_dashboard"] = True
                context["data_source"] = f"{data_source}+dashboard_fallback"
                use_dashboard = True
            except Exception as fallback_exc:
                logger.log(f"dashboard 回退也失败: {fallback_exc}", "WARN")
                context["snapshot_reason"] = f"snapshot_fetch_failed:{type(exc).__name__}"
                return matches, context
        else:
            context["snapshot_reason"] = f"snapshot_fetch_failed:{type(exc).__name__}"
            return matches, context

    if use_dashboard and dashboard_scope and not dashboard_scope.get("warmed", True):
        logger.log(
            f"watch巡检当前回退到 dashboard，但 dashboard 新范围快照未就绪: {dashboard_scope.get('warm_reason', 'unknown')}",
            "WARN",
        )
        context["snapshot_reason"] = "dashboard_scope_not_warmed"
        return matches, context
    if not snapshot_rows and not use_dashboard:
        try:
            logger.log("直连快照为空，尝试回退到本地 dashboard 快照", "WARN")
            snapshot_rows = fetch_live_data_snapshot(
                None,
                None,
                gtypes=target_gtypes or ALL_GTYPES,
                use_dashboard=True,
                feed_url=feed_url or DEFAULT_URL,
            )
            context["snapshot_rows_count"] = len(snapshot_rows)
            context["use_dashboard"] = True
            context["data_source"] = f"{data_source}+dashboard_fallback"
            use_dashboard = True
        except Exception as fallback_exc:
            logger.log(f"dashboard 空快照回退失败: {fallback_exc}", "WARN")
    if snapshot_rows:
        logger.log(f"watch巡检快照: {len(snapshot_rows)} 条")
        bind_selected_matches_to_feed(matches, snapshot_rows, logger)
        context["snapshot_healthy"] = True
        context["snapshot_reason"] = "snapshot_rows_available"
    else:
        logger.log("watch巡检未拿到实时快照", "WARN")
        context["snapshot_reason"] = "snapshot_rows_empty"

    return matches, context


def active_match_signatures(state: dict, target_id: str | None = None) -> set[str]:
    signatures: set[str] = set()
    for payload in (state.get("active_locks") or {}).values():
        if target_id and payload.get("target_id") != target_id:
            continue
        for signature in payload.get("match_signatures") or []:
            if signature:
                signatures.add(signature)
    return signatures


def active_stream_count(state: dict, target_id: str | None = None) -> int:
    total = 0
    for payload in (state.get("active_locks") or {}).values():
        if target_id and payload.get("target_id") != target_id:
            continue
        signatures = [signature for signature in (payload.get("match_signatures") or []) if signature]
        if signatures:
            total += len(signatures)
        else:
            selected = payload.get("selected") or []
            total += max(1, len(selected))
    return total


def collect_running_session_signatures(output_root: Path, logger: SessionLogger) -> set[str]:
    signatures: set[str] = set()
    if not output_root.exists():
        return signatures
    session_dirs = list(output_root.glob("session_*"))
    if not session_dirs:
        session_dirs = list(output_root.glob("*/session_*"))
    for session_dir in sorted(session_dirs):
        if not session_dir.is_dir():
            continue
        if (session_dir / "session_result.json").exists():
            continue
        processes = find_session_processes(session_dir)
        alive = False
        for line in processes:
            pid_text = str(line).split(None, 1)[0] if line else ""
            try:
                pid = int(pid_text)
            except Exception:
                pid = 0
            if is_pid_alive(pid):
                alive = True
                break
        if not alive:
            continue
        payload = load_json(session_dir / "watch_selected_matches.json") or {}
        for match in payload.get("selected_matches") or []:
            try:
                signature = target_match_signature(match)
            except Exception:
                signature = ""
            if signature:
                signatures.add(signature)
    if signatures:
        logger.log(f"检测到 {len(signatures)} 场比赛已被现存录制会话占用，后续巡检会跳过这些场次以避免重复开录")
    return signatures


def choose_target_matches(
    matches: list[dict],
    target: dict,
    logger: SessionLogger,
    *,
    exclude_signatures: set[str] | None = None,
    limit: int | None = None,
) -> tuple[list[dict], str]:
    # Every watch tick re-evaluates all matches that are still live right now.
    # active_locks only prevents duplicate launches for already-recording matches;
    # it must not block matches that were previously unbound but become bindable later.
    candidates = [match.copy() for match in matches if match_target_rule(match, target)]
    if not candidates:
        return [], ""
    candidates = filter_matches_ready_to_record(candidates, 1, logger)
    if not candidates:
        return [], ""
    playable = [match for match in candidates if str(match.get("watch_url", "")).strip()]
    if playable:
        skipped = len(candidates) - len(playable)
        if skipped > 0:
            logger.log(
                f"目标 {target.get('name', target.get('id', 'unknown'))}: "
                f"跳过 {skipped} 场无 watch_url 的不可播放候选",
                "WARN",
            )
        candidates = playable
    else:
        logger.log(
            f"目标 {target.get('name', target.get('id', 'unknown'))}: 当前没有可直接打开的直播链接，跳过本轮",
            "WARN",
        )
        return [], ""
    excluded = exclude_signatures or set()
    if excluded:
        candidates = [match for match in candidates if target_match_signature(match) not in excluded]
    if not candidates:
        return [], ""

    candidates.sort(
        key=lambda match: (
            0 if (match.get("gid") or match.get("ecid")) else 1,
            -int(((match.get("_feed_binding") or {}).get("score", 0) or 0)),
            match.get("league", ""),
            match.get("team_h", ""),
            match.get("team_c", ""),
        )
    )

    bound = []
    unbound = []
    for match in candidates:
        if match.get("gid") or match.get("ecid"):
            match["data_binding_status"] = "bound"
            match["recording_note"] = ""
            bound.append(match)
        else:
            match["data_binding_status"] = "unbound"
            match["recording_note"] = "测试流：命中目标比赛，但当前未匹配到实时数据"
            unbound.append(match)

    max_streams = max(1, int(limit or target.get("max_streams", 1)))
    selected = bound[:max_streams]
    if len(selected) < max_streams and target.get("allow_test_recording", True):
        selected.extend(unbound[: max_streams - len(selected)])

    if not selected:
        return [], ""
    if all(match.get("data_binding_status") == "bound" for match in selected):
        mode = "data_bound"
    elif all(match.get("data_binding_status") == "unbound" for match in selected):
        mode = "test_only"
    else:
        mode = "mixed"
    return selected, mode


def session_is_active(session_dir: Path, run_pid: object) -> bool:
    if (session_dir / "session_result.json").exists():
        return False
    if is_pid_alive(run_pid):
        return True
    return bool(find_session_processes(session_dir, recording_only=True))


def reconcile_active_locks(state: dict, logger: SessionLogger) -> dict:
    active = state.get("active_locks") or {}
    retained = {}
    finished = []
    for lock_key, payload in active.items():
        session_dir = Path(payload.get("session_dir", ""))
        if session_dir and session_is_active(session_dir, payload.get("run_pid")):
            retained[lock_key] = payload
            continue
        payload["released_at"] = now_iso()
        finished.append(payload)
        logger.log(f"释放已完成锁: {lock_key} -> {session_dir}")
    state["active_locks"] = retained
    if finished:
        history = state.get("history") or []
        history.extend(finished)
        state["history"] = history[-50:]
    return state


def launch_target_recording(
    target: dict,
    selected: list[dict],
    trigger_mode: str,
    job_id: str,
    lock_key: str,
    output_root: Path,
    dry_run: bool,
) -> dict:
    session_id = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    session_dir = build_session_dir(output_root, session_id)
    session_dir.mkdir(parents=True, exist_ok=True)
    selected_path = session_dir / "watch_selected_matches.json"
    save_json(selected_path, {"selected_matches": selected})
    save_json(
        session_dir / "watch_runtime.json",
        {
            "watch_job_id": job_id,
            "trigger_reason": f"target:{target['id']}",
            "target_match_rule_source": target.get("rule_source", "config"),
            "trigger_mode": trigger_mode,
            "session_lock_metadata": {
                "watch_lock_key": lock_key,
                "target_id": target["id"],
                "target_name": target["name"],
                "match_signatures": [target_match_signature(match) for match in selected],
            },
            "progress_snapshots": [],
            "final_notify": {
                "sent": False,
                "at": "",
                "channel": "",
                "target": "",
                "account": "",
                "rc": None,
            },
        },
    )

    cmd = [
        "python3",
        str(LAUNCHER_PATH),
        "--duration-minutes",
        str(target.get("duration_minutes", 30)),
        "--max-streams",
        str(len(selected)),
        "--browser",
        target.get("browser", "safari"),
        "--session-id",
        session_id,
        "--selected-matches-file",
        str(selected_path),
        "--watch-job-id",
        job_id,
        "--trigger-reason",
        f"target:{target['id']}",
        "--match-rule-source",
        target.get("rule_source", "config"),
        "--trigger-mode",
        trigger_mode,
        "--watch-lock-key",
        lock_key,
        "--progress-interval-minutes",
        str(target.get("progress_interval_minutes", DEFAULT_PROGRESS_INTERVAL_MINUTES)),
        "--notify-channel",
        target.get("notify_channel", DEFAULT_NOTIFY_CHANNEL),
        "--notify-account",
        target.get("notify_account", DEFAULT_NOTIFY_ACCOUNT),
        "--notify-target",
        target.get("notify_target", DEFAULT_NOTIFY_TARGET),
        "--notify-title",
        f"录制任务已结束。{target['name']}",
    ]
    gtypes = ",".join(sorted({match.get("gtype", "") for match in selected if match.get("gtype")}))
    if gtypes:
        cmd.extend(["--gtypes", gtypes])
    if dry_run:
        cmd.append("--dry-run")

    completed = subprocess.run(cmd, capture_output=True, text=True, check=False)
    stdout = completed.stdout.strip()
    if completed.returncode != 0:
        raise RuntimeError(f"launcher失败: rc={completed.returncode} stderr={completed.stderr.strip()}")
    if not stdout:
        raise RuntimeError("launcher 未返回 JSON")
    payload = json.loads(stdout)
    payload["selected_matches_file"] = str(selected_path)
    payload["selected_matches"] = selected
    payload["watch_lock_key"] = lock_key
    return payload


def summarize_selected(selected: list[dict]) -> list[str]:
    lines = []
    for match in selected:
        label = " vs ".join(part for part in [match.get("team_h", ""), match.get("team_c", "")] if part) or match.get("league", "unknown")
        binding = match.get("data_binding_status", "unknown")
        lines.append(f"{label} ({binding})")
    return lines


def run_watch_cycle(args: argparse.Namespace, logger: SessionLogger) -> dict:
    config_path = Path(args.config)
    config = load_watch_config(config_path)
    defaults, targets = resolve_targets(config, args)
    effective_gtypes = resolve_watch_gtypes(targets, defaults)
    interval_minutes = int(args.interval_minutes or defaults["check_interval_minutes"] or DEFAULT_CHECK_INTERVAL_MINUTES)
    args.interval_minutes = interval_minutes
    save_watch_service_state(args, effective_gtypes)
    dashboard_scope = sync_live_dashboard_scope(logger, args.job_id, effective_gtypes)
    deadline = resolve_deadline(args)
    state = load_watch_state(args.job_id, config_path, interval_minutes)
    state["stop_at"] = deadline_to_iso(deadline)
    state = reconcile_active_locks(state, logger)
    running_session_signatures = collect_running_session_signatures(
        Path(args.output_root or DEFAULT_OUTPUT_ROOT),
        logger,
    )
    if not targets:
        if not args.dry_run:
            save_watch_state(args.job_id, state)
        return {
            "job_id": args.job_id,
            "config_path": str(config_path),
            "checked_at": now_iso(),
            "stop_at": deadline_to_iso(deadline),
            "targets": [],
            "launched": [],
            "active_locks": state.get("active_locks", {}),
            "discovered_matches": 0,
        }

    all_gtypes = effective_gtypes or ALL_GTYPES
    matches, context = discover_and_bind_matches(
        logger,
        args.browser or defaults["browser"],
        all_gtypes,
        dashboard_scope=dashboard_scope,
    )
    if matches:
        logger.log(
            f"本轮巡检会重新比对当前仍在直播的全部 {len(matches)} 场比赛；"
            "active_locks 仅用于防止重复开录，不会阻止之前未绑定的比赛在后续轮次重新匹配"
        )
    launched = []
    launched_signatures = set()
    configured_global_limit = int(defaults.get("max_streams", 0) or 0)
    global_limit = configured_global_limit if configured_global_limit > 0 else 10**9

    if matches and not context.get("snapshot_healthy"):
        logger.log(
            f"数据源健康检查未通过，跳过本轮启动录制: "
            f"source={context.get('data_source','')} reason={context.get('snapshot_reason','unknown')}",
            "WARN",
        )
        if not args.dry_run:
            save_watch_state(args.job_id, state)
        return {
            "job_id": args.job_id,
            "config_path": str(config_path),
            "checked_at": now_iso(),
            "stop_at": deadline_to_iso(deadline),
            "targets": [target["id"] for target in targets],
            "launched": [],
            "active_locks": state.get("active_locks", {}),
            "discovered_matches": len(matches),
            "data_source_health": {
                "source": context.get("data_source", ""),
                "healthy": False,
                "reason": context.get("snapshot_reason", "unknown"),
                "snapshot_rows_count": context.get("snapshot_rows_count", 0),
            },
            "dashboard_scope": dashboard_scope,
        }

    for target in targets:
        active_for_target = active_stream_count(state, target["id"])
        active_total = active_stream_count(state)
        launched_total = sum(len(item.get("match_signatures") or []) or max(1, len(item.get("selected") or [])) for item in launched)
        configured_target_limit = int(target.get("max_streams", 0) or 0)
        target_limit = configured_target_limit if configured_target_limit > 0 else 10**9
        remaining_slots = min(
            target_limit - active_for_target,
            global_limit - active_total - launched_total,
        )
        if remaining_slots <= 0:
            continue
        excluded = active_match_signatures(state, target["id"]) | launched_signatures | running_session_signatures
        selected, trigger_mode = choose_target_matches(
            matches,
            target,
            logger,
            exclude_signatures=excluded,
            limit=remaining_slots,
        )
        if not selected:
            continue
        signatures = {target_match_signature(match) for match in selected}
        if signatures & launched_signatures:
            logger.log(f"跳过重复命中的目标: {target['name']}", "WARN")
            continue
        launched_signatures |= signatures
        session_id_preview = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        lock_key = f"{target['id']}::{session_id_preview}"
        payload = launch_target_recording(
            target,
            selected,
            trigger_mode,
            job_id=args.job_id,
            lock_key=lock_key,
            output_root=Path(args.output_root or DEFAULT_OUTPUT_ROOT),
            dry_run=args.dry_run,
        )
        lock_payload = {
            "lock_key": lock_key,
            "target_id": target["id"],
            "target_name": target["name"],
            "session_id": payload.get("session_id"),
            "session_dir": payload.get("session_dir"),
            "run_pid": payload.get("run_pid"),
            "progress_pid": payload.get("progress_pid"),
            "trigger_mode": trigger_mode,
            "rule_source": target.get("rule_source", "config"),
            "match_signatures": sorted(signatures),
            "selected": summarize_selected(selected),
            "started_at": payload.get("started_at") or now_iso(),
        }
        if not args.dry_run:
            state.setdefault("active_locks", {})[lock_key] = lock_payload
        launched.append(lock_payload)
        logger.log(
            f"已触发目标录制: {target['name']} -> {payload.get('session_dir')} "
            f"({trigger_mode}, {len(selected)}路)"
        )

    if not args.dry_run:
        save_watch_state(args.job_id, state)
    return {
        "job_id": args.job_id,
        "config_path": str(config_path),
        "checked_at": now_iso(),
        "stop_at": deadline_to_iso(deadline),
        "targets": [target["id"] for target in targets],
        "launched": launched,
        "active_locks": state.get("active_locks", {}),
        "discovered_matches": len(matches),
        "data_source_health": {
            "source": context.get("data_source", ""),
            "healthy": bool(context.get("snapshot_healthy")),
            "reason": context.get("snapshot_reason", ""),
            "snapshot_rows_count": context.get("snapshot_rows_count", 0),
        },
        "dashboard_scope": dashboard_scope,
    }


def main() -> int:
    argv = sys.argv[1:]
    parser = argparse.ArgumentParser(description="定时巡检目标比赛，并在命中时自动启动录制。")
    parser.add_argument("--config", default=str(DEFAULT_CONFIG_PATH))
    parser.add_argument("--job-id", default="default_watch")
    parser.add_argument("--browser", default="")
    parser.add_argument("--output-root", default=str(DEFAULT_OUTPUT_ROOT))
    parser.add_argument("--interval-minutes", type=int, default=0)
    parser.add_argument("--check-once", action="store_true")
    parser.add_argument("--loop", action="store_true")
    parser.add_argument("--override-match-query", action="append", default=[])
    parser.add_argument("--override-replace", action="store_true")
    parser.add_argument("--gtypes", default="")
    parser.add_argument("--duration-minutes", type=int, default=0)
    parser.add_argument("--max-streams", type=int, default=0)
    parser.add_argument("--progress-interval-minutes", type=int, default=0)
    parser.add_argument("--stop-at", default="", help="本地时间截止点，格式如 2026-03-25 23:00")
    parser.add_argument("--max-runtime-minutes", type=int, default=0, help="watcher 最长运行分钟数")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    args._provided_flags = {token for token in argv if token.startswith("--")}

    if not args.check_once and not args.loop:
        args.check_once = True

    WATCH_RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
    log_path = WATCH_RUNTIME_DIR / f"{args.job_id}.log"
    logger = SessionLogger(str(log_path))

    try:
        if args.check_once:
            payload = run_watch_cycle(args, logger)
            print(json.dumps(payload, ensure_ascii=False, indent=2))
            return 0

        config = load_watch_config(Path(args.config))
        defaults = build_target_defaults(config)
        sleep_seconds = max(60, int((args.interval_minutes or defaults["check_interval_minutes"]) * 60))
        deadline = resolve_deadline(args)
        if deadline:
            logger.log(f"watch巡检将自动停止于: {deadline_to_iso(deadline)}")
        while True:
            if deadline and datetime.now() >= deadline:
                logger.log("已到达 watch 巡检截止时间，停止循环。")
                break
            payload = run_watch_cycle(args, logger)
            print(json.dumps(payload, ensure_ascii=False, indent=2), flush=True)
            if deadline and datetime.now() >= deadline:
                logger.log("本轮巡检完成后已到达截止时间，停止循环。")
                break
            time.sleep(sleep_seconds)
        return 0
    finally:
        mark_watch_service_stopped(args.job_id)
        logger.close()


if __name__ == "__main__":
    raise SystemExit(main())
