#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
import hashlib
from urllib.parse import quote
from urllib.request import urlopen
from urllib.error import URLError

SCRIPT_PATH = Path(__file__).resolve()
SCRIPT_DIR = SCRIPT_PATH.parent
RECORDINGS_DIR = SCRIPT_DIR.parent
if str(RECORDINGS_DIR) not in sys.path:
    sys.path.insert(0, str(RECORDINGS_DIR))

from run_auto_capture import (
    ALL_GTYPES,
    DEFAULT_URL,
    SessionLogger,
    annotate_selected_matches_for_recording,
    bind_selected_matches_to_feed,
    bootstrap_credentials,
    discover_live_matches,
    discover_live_matches_from_schedule,
    ensure_schedules_live_ready,
    fetch_live_data_snapshot,
    filter_matches_ready_to_record,
    find_schedules_tab,
    get_browser_app,
    infer_gtype_from_league,
    list_watch_tabs,
    load_selected_matches_file,
    parse_watch_candidates_payload,
    prioritize_selected_matches,
    require_bound_data_matches,
)


DEFAULT_SEGMENT_MINUTES = 5
WATCH_BOOTSTRAP_FETCH_TIMEOUT = 60
WATCH_BOOTSTRAP_ASYNC_FETCH_TIMEOUT = 20
WATCH_BOOTSTRAP_RETRY_ATTEMPTS = 3
WATCH_BOOTSTRAP_RETRY_DELAY_SECONDS = 1.5
APP_WEB_BRIDGE_URL = os.environ.get("MATCH_PLAN_APP_WEB_BRIDGE_URL", "http://127.0.0.1:18765").rstrip("/")
APP_WEB_BRIDGE_FALLBACK_TO_BROWSER = (
    str(os.environ.get("MATCH_PLAN_APP_WEB_BRIDGE_FALLBACK_TO_BROWSER", "")).strip().lower()
    in {"1", "true", "yes", "on"}
)


def now_iso() -> str:
    return datetime.now().isoformat()


def applescript_eval_js(browser: str, window_index: int, tab_index: int, js: str, timeout: int = 20) -> str:
    app = get_browser_app(browser)
    js_literal = json.dumps(js)
    if browser == "safari":
        script = f'''
            tell application "{app}"
                return do JavaScript {js_literal} in tab {tab_index} of window {window_index}
            end tell
        '''
    else:
        script = f'''
            tell application "{app}"
                return execute tab {tab_index} of window {window_index} javascript {js_literal}
            end tell
        '''
    result = subprocess.run(
        ["osascript", "-e", script],
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    if result.returncode != 0:
        raise RuntimeError((result.stderr or result.stdout).strip() or "AppleScript failed")
    return result.stdout.strip()


def extract_inline_var_from_html(html: str, name: str) -> str:
    line_re = re.compile(
        rf"""^(?!\s*//)\s*(?:var|let)\s+{re.escape(name)}\s*=\s*(?:"([^"]*)"|'([^']*)'|(\d+))""",
        re.MULTILINE,
    )
    matches = list(line_re.finditer(html or ""))
    if not matches:
        return ""
    match = matches[-1]
    return next((group for group in match.groups() if group is not None), "") or ""


def parse_watch_bootstrap_html(html: str, watch_url: str = "") -> dict:
    html = html or ""
    title_match = re.search(r"<title>(.*?)</title>", html, re.IGNORECASE | re.DOTALL)
    title = title_match.group(1).strip() if title_match else ""
    return {
        "title": title,
        "url": watch_url,
        "serverMs": extract_inline_var_from_html(html, "serverMs"),
        "serverHost": extract_inline_var_from_html(html, "serverHost"),
        "roomId": extract_inline_var_from_html(html, "roomId"),
        "token": extract_inline_var_from_html(html, "token"),
        "uEmail": extract_inline_var_from_html(html, "uEmail"),
        "source": "schedules_fetch_html",
    }


def fetch_watch_html_via_app_bridge(watch_url: str, timeout: int = WATCH_BOOTSTRAP_FETCH_TIMEOUT) -> dict:
    target = watch_url.rstrip("/")
    request_url = f"{APP_WEB_BRIDGE_URL}/fetch-watch?watch_url={quote(target, safe='')}"
    with urlopen(request_url, timeout=timeout) as response:
        raw = response.read().decode("utf-8", errors="ignore")
    payload = json.loads(raw or "{}")
    if not isinstance(payload, dict):
        raise RuntimeError("app web bridge payload invalid")
    payload["watch_url"] = target
    return payload


def fetch_live_watch_candidates_via_app_bridge(timeout: int = WATCH_BOOTSTRAP_FETCH_TIMEOUT) -> list[dict]:
    request_url = f"{APP_WEB_BRIDGE_URL}/discover-live"
    with urlopen(request_url, timeout=timeout) as response:
        raw = response.read().decode("utf-8", errors="ignore")
    return parse_watch_candidates_payload(raw, logger=_NullLogger(), source_label="App 内嵌页")


def fetch_app_bridge_page_state(timeout: int = WATCH_BOOTSTRAP_FETCH_TIMEOUT) -> dict:
    request_url = f"{APP_WEB_BRIDGE_URL}/page-state"
    with urlopen(request_url, timeout=timeout) as response:
        raw = response.read().decode("utf-8", errors="ignore")
    payload = json.loads(raw or "{}")
    if not isinstance(payload, dict):
        raise RuntimeError("app web bridge page-state payload invalid")
    return payload


def app_bridge_session_ready() -> tuple[bool, dict]:
    try:
        payload = fetch_app_bridge_page_state()
    except (URLError, TimeoutError, OSError, json.JSONDecodeError) as exc:
        return False, {"ok": False, "error": str(exc), "source": "app_web_bridge"}
    ready = bool(
        payload.get("ok")
        and payload.get("webViewReady")
        and not payload.get("loginRequired")
        and payload.get("hasLivePane")
    )
    return ready, payload


def fetch_watch_html_via_schedules(browser: str, watch_url: str) -> dict:
    schedules_tab = find_schedules_tab(browser)
    if not schedules_tab:
        raise RuntimeError("未找到 schedules/live 页面")
    target = watch_url.rstrip("/")
    js = f"""
    (() => {{
      const xhr = new XMLHttpRequest();
      xhr.open('GET', {json.dumps(target)}, false);
      xhr.withCredentials = true;
      xhr.setRequestHeader('Cache-Control', 'no-cache');
      xhr.send(null);
      return JSON.stringify({{
        status: xhr.status,
        responseURL: xhr.responseURL || '',
        html: xhr.responseText || ''
      }});
    }})()
    """
    raw = applescript_eval_js(
        browser,
        int(schedules_tab["window_index"]),
        int(schedules_tab["tab_index"]),
        js,
        timeout=WATCH_BOOTSTRAP_FETCH_TIMEOUT,
    )
    payload = json.loads(raw or "{}")
    payload["watch_url"] = target
    return payload


def fetch_watch_html_via_schedules_async(browser: str, watch_url: str) -> dict:
    schedules_tab = find_schedules_tab(browser)
    if not schedules_tab:
        raise RuntimeError("未找到 schedules/live 页面")
    target = watch_url.rstrip("/")
    fetch_key = f"__codexWatchFetch_{hashlib.sha1(target.encode('utf-8')).hexdigest()[:12]}"
    kickoff_js = f"""
    (() => {{
      const key = {json.dumps(fetch_key)};
      window[key] = {{done:false, status:0, responseURL:'', html:'', error:''}};
      fetch({json.dumps(target)}, {{
        method: 'GET',
        credentials: 'include',
        cache: 'no-store'
      }}).then(async (resp) => {{
        const html = await resp.text();
        window[key] = {{
          done: true,
          status: resp.status || 0,
          responseURL: resp.url || '',
          html: html || '',
          error: ''
        }};
      }}).catch((err) => {{
        window[key] = {{
          done: true,
          status: 0,
          responseURL: '',
          html: '',
          error: String(err || '')
        }};
      }});
      return JSON.stringify({{started:true, key}});
    }})()
    """
    applescript_eval_js(
        browser,
        int(schedules_tab["window_index"]),
        int(schedules_tab["tab_index"]),
        kickoff_js,
        timeout=WATCH_BOOTSTRAP_FETCH_TIMEOUT,
    )
    poll_js = f"""
    (() => {{
      const payload = window[{json.dumps(fetch_key)}] || {{}};
      return JSON.stringify(payload);
    }})()
    """
    deadline = time.time() + WATCH_BOOTSTRAP_ASYNC_FETCH_TIMEOUT
    last_payload = {"done": False, "status": 0, "responseURL": "", "html": "", "error": ""}
    while time.time() < deadline:
        raw = applescript_eval_js(
            browser,
            int(schedules_tab["window_index"]),
            int(schedules_tab["tab_index"]),
            poll_js,
            timeout=WATCH_BOOTSTRAP_FETCH_TIMEOUT,
        )
        payload = json.loads(raw or "{}")
        if isinstance(payload, dict):
            last_payload = payload
        if payload.get("done"):
            break
        time.sleep(0.5)
    last_payload["watch_url"] = target
    return last_payload


def extract_livekit_bootstrap_from_schedules(browser: str, watch_url: str) -> dict:
    last_payload: dict = {}
    for attempt in range(1, WATCH_BOOTSTRAP_RETRY_ATTEMPTS + 1):
        payload = {}
        bridge_error = None
        try:
            payload = fetch_watch_html_via_app_bridge(watch_url)
        except Exception as exc:
            bridge_error = exc
        if not payload:
            payload = fetch_watch_html_via_schedules(browser, watch_url)
        status = int(payload.get("status") or 0)
        html = str(payload.get("html") or "")
        if status != 200 or not html:
            if bridge_error is None:
                payload = {}
            if not payload and APP_WEB_BRIDGE_FALLBACK_TO_BROWSER:
                payload = fetch_watch_html_via_schedules_async(browser, watch_url)
            status = int(payload.get("status") or 0)
            html = str(payload.get("html") or "")
        if status == 200 and html:
            bootstrap = parse_watch_bootstrap_html(html, watch_url=watch_url)
            bootstrap["responseURL"] = str(payload.get("responseURL") or "")
            bootstrap["source"] = str(payload.get("source") or bootstrap.get("source") or "unknown")
            return bootstrap
        last_payload = payload
        if attempt < WATCH_BOOTSTRAP_RETRY_ATTEMPTS:
            if APP_WEB_BRIDGE_FALLBACK_TO_BROWSER:
                try:
                    ensure_schedules_live_ready(browser, None)
                except Exception:
                    pass
            time.sleep(WATCH_BOOTSTRAP_RETRY_DELAY_SECONDS)
    raise RuntimeError(
        f"schedules 页面 fetch watch 失败: status={int(last_payload.get('status') or 0)}"
        + (f" error={last_payload.get('error', '')}" if last_payload.get("error") else "")
    )


class _NullLogger:
    def log(self, *_args, **_kwargs):
        return None


def discover_live_matches_from_app_bridge(logger: SessionLogger) -> dict:
    try:
        candidates = fetch_live_watch_candidates_via_app_bridge()
    except Exception as exc:
        logger.log(f"App bridge 直播列表抓取失败: {exc}", "WARN")
        return {}
    if not candidates:
        return {}
    grouped: dict[str, list[dict]] = {}
    for item in candidates:
        league = str(item.get("league", "")).strip()
        home = str(item.get("home", "")).strip()
        away = str(item.get("away", "")).strip()
        gtype = infer_gtype_from_league(league)
        grouped.setdefault(gtype, []).append({
            "gid": "",
            "ecid": "",
            "gtype": gtype,
            "league": league,
            "team_h": home,
            "team_c": away,
            "score_h": "",
            "score_c": "",
            "watch_url": item.get("href", ""),
        })
    total = sum(len(v) for v in grouped.values())
    logger.log(
        f"App 内嵌页: 发现 {total} 场可观看直播: "
        f"{', '.join(f'{k}({len(v)})' for k, v in sorted(grouped.items()))}"
    )
    return grouped


def extract_stream_bootstrap_from_tab(browser: str, window_index: int, tab_index: int) -> dict:
    js = """
    (() => JSON.stringify({
      title: document.title,
      url: location.href,
      serverMs: typeof serverMs === 'undefined'
        ? (typeof window.serverMs === 'undefined' ? '' : window.serverMs)
        : serverMs,
      serverHost: typeof serverHost === 'undefined'
        ? (typeof window.serverHost === 'undefined' ? '' : window.serverHost)
        : serverHost,
      token: typeof token === 'undefined'
        ? (typeof window.token === 'undefined' ? '' : window.token)
        : token,
      readyState: document.readyState,
      hasVideo: !!document.querySelector('video'),
      hasAudio: !!document.querySelector('audio')
    }))();
    """
    raw = applescript_eval_js(browser, window_index, tab_index, js)
    payload = json.loads(raw or "{}")
    payload["window_index"] = window_index
    payload["tab_index"] = tab_index
    return payload


def extract_best_livekit_bootstrap(browser: str, ready_tab: dict) -> dict:
    primary = extract_stream_bootstrap_from_tab(
        browser,
        int(ready_tab["window_index"]),
        int(ready_tab["tab_index"]),
    )
    server_ms = str(primary.get("serverMs", "")).strip().lower()
    if server_ms == "lk" and primary.get("serverHost") and primary.get("token"):
        return primary

    watch_url = str(ready_tab.get("url", "")).rstrip("/")
    if not watch_url:
        return primary

    for candidate in list_watch_tabs(browser):
        candidate_url = str(candidate.get("url", "")).rstrip("/")
        if candidate_url != watch_url:
            continue
        try:
            payload = extract_stream_bootstrap_from_tab(
                browser,
                int(candidate["window_index"]),
                int(candidate["tab_index"]),
            )
        except Exception:
            continue
        candidate_server_ms = str(payload.get("serverMs", "")).strip().lower()
        if candidate_server_ms == "lk" and payload.get("serverHost") and payload.get("token"):
            return payload

    return primary


def extract_best_livekit_bootstrap_for_watch_url(
    browser: str,
    watch_url: str,
    ready_tab: dict | None = None,
    logger: SessionLogger | None = None,
) -> dict:
    if watch_url:
        try:
            bootstrap = extract_livekit_bootstrap_from_schedules(browser, watch_url)
            server_ms = str(bootstrap.get("serverMs", "")).strip().lower()
            if server_ms == "lk" and bootstrap.get("serverHost") and bootstrap.get("token"):
                if logger:
                    logger.log(f"通过 schedules/live 直取 bootstrap 成功: {watch_url}")
                return bootstrap
            if logger:
                logger.log(
                    f"schedules/live 直取 bootstrap 不完整: {watch_url} "
                    f"(serverMs={server_ms or 'unknown'})",
                    "WARN",
                )
        except Exception as exc:
            if logger:
                logger.log(f"schedules/live 直取 bootstrap 失败: {watch_url} | {exc}", "WARN")

    if ready_tab:
        return extract_best_livekit_bootstrap(browser, ready_tab)
    return {"url": watch_url}


def load_manifest(match_dir: Path) -> dict | None:
    path = match_dir / "manifest.json"
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def probe_media_format(path: Path) -> dict:
    if not path.exists():
        return {}
    result = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "format=format_name,duration",
            "-of",
            "json",
            str(path),
        ],
        capture_output=True,
        text=True,
        timeout=30,
    )
    if result.returncode != 0:
        return {}
    try:
        return json.loads(result.stdout or "{}").get("format") or {}
    except Exception:
        return {}


def normalize_full_output_to_mp4(video_path: Path, logger: SessionLogger) -> Path | None:
    if not video_path.exists():
        return None
    fmt = probe_media_format(video_path)
    format_name = str(fmt.get("format_name", "")).lower()
    if "mov" in format_name or "mp4" in format_name:
        return video_path

    normalized_path = video_path.with_suffix(".normalized.mp4")
    cmd = [
        "ffmpeg",
        "-y",
        "-loglevel",
        "error",
        "-i",
        str(video_path),
        "-map",
        "0:v:0",
        "-an",
        "-c:v",
        "libx264",
        "-preset",
        "veryfast",
        "-crf",
        "23",
        "-pix_fmt",
        "yuv420p",
        "-movflags",
        "+faststart",
        str(normalized_path),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
    if result.returncode != 0 or not normalized_path.exists() or normalized_path.stat().st_size == 0:
        logger.log(
            f"直连 full.mp4 规范化失败: {(result.stderr or result.stdout).strip()[:300]}",
            "WARN",
        )
        return video_path
    os.replace(normalized_path, video_path)
    logger.log(f"直连成品已正规化为 MP4: {video_path.name}")
    return video_path


def resolve_selected_matches(args, logger: SessionLogger) -> tuple[list[dict], tuple]:
    bridge_ready, bridge_state = app_bridge_session_ready()
    if bridge_ready:
        logger.log(
            f"App 内嵌页已就绪: {bridge_state.get('currentURL') or '-'} | live={bridge_state.get('liveCandidateCount') or 0}"
        )
    else:
        logger.log(
            "App 内嵌页暂未就绪，改走浏览器 fallback"
            if APP_WEB_BRIDGE_FALLBACK_TO_BROWSER
            else "App 内嵌页暂未就绪，且已禁用外部浏览器 fallback",
            "WARN",
        )
        if not APP_WEB_BRIDGE_FALLBACK_TO_BROWSER:
            return [], (None, None, False, DEFAULT_URL, "app_bridge_unavailable")
        if not ensure_schedules_live_ready(logger, args.browser):
            return [], (None, None, False, DEFAULT_URL, "schedule_unavailable")

    if args.skip_data_binding:
        cookie = None
        template = None
        use_dashboard = False
        feed_url = DEFAULT_URL
        data_source = "schedule_only_no_binding"
    else:
        cookie, template, use_dashboard, feed_url, data_source = bootstrap_credentials(logger, args.browser)
    logger.log(f"数据源模式: {data_source}")

    selected = []
    explicit_selected = load_selected_matches_file(args.selected_matches_file, logger)
    if explicit_selected:
        selected = explicit_selected
    else:
        all_matches = discover_live_matches_from_app_bridge(logger) if bridge_ready else {}
        if not all_matches and APP_WEB_BRIDGE_FALLBACK_TO_BROWSER:
            all_matches = discover_live_matches_from_schedule(logger, args.browser)
        if not all_matches and not args.skip_data_binding and APP_WEB_BRIDGE_FALLBACK_TO_BROWSER:
            all_matches = discover_live_matches(
                cookie,
                template,
                ALL_GTYPES,
                logger,
                use_dashboard,
                feed_url=feed_url or DEFAULT_URL,
            )
        if not all_matches:
            return [], (cookie, template, use_dashboard, feed_url, data_source)

        if args.match_query:
            selected = []
            query = args.match_query.lower()
            for matches in all_matches.values():
                for item in matches:
                    label = " ".join([
                        str(item.get("league", "")),
                        str(item.get("team_h", "")),
                        str(item.get("team_c", "")),
                    ]).lower()
                    if query in label:
                        selected.append(item)
            logger.log(f"按关键词选择 {args.match_query!r} → 候选 {len(selected)} 场")
        elif args.gtypes:
            wanted = [g.strip().upper() for g in args.gtypes.split(",") if g.strip()]
            for g in wanted:
                selected.extend(all_matches.get(g, []))
            logger.log(f"命令行选择 {wanted} → 候选 {len(selected)} 场")
        elif args.all:
            for matches in all_matches.values():
                selected.extend(matches)
            logger.log(f"录制全部候选 {len(selected)} 场")
        else:
            for matches in all_matches.values():
                selected.extend(matches)
            logger.log(f"默认录制候选 {len(selected)} 场")

    if not selected:
        return [], (cookie, template, use_dashboard, feed_url, data_source)

    if args.skip_data_binding:
        selected = annotate_selected_matches_for_recording(selected)
    else:
        try:
            snapshot_rows = fetch_live_data_snapshot(
                cookie,
                template,
                gtypes=list({m.get("gtype") for m in selected if m.get("gtype")}) or ALL_GTYPES,
                use_dashboard=use_dashboard,
                feed_url=feed_url or DEFAULT_URL,
            )
            logger.log(f"当前数据快照: {len(snapshot_rows)} 条候选比赛")
            if (
                not snapshot_rows
                and str(os.environ.get("MATCH_PLAN_SHARED_DATA_CREDENTIALS_FILE", "")).strip()
                and not use_dashboard
            ):
                logger.log("共享数据源凭证返回 0 条快照，回退一次实时登录刷新凭证", "WARN")
                shared_path = os.environ.pop("MATCH_PLAN_SHARED_DATA_CREDENTIALS_FILE", None)
                try:
                    cookie, template, use_dashboard, feed_url, data_source = bootstrap_credentials(logger, args.browser)
                finally:
                    if shared_path:
                        os.environ["MATCH_PLAN_SHARED_DATA_CREDENTIALS_FILE"] = shared_path
                logger.log(f"刷新后数据源模式: {data_source}")
                snapshot_rows = fetch_live_data_snapshot(
                    cookie,
                    template,
                    gtypes=list({m.get('gtype') for m in selected if m.get('gtype')}) or ALL_GTYPES,
                    use_dashboard=use_dashboard,
                    feed_url=feed_url or DEFAULT_URL,
                )
                logger.log(f"刷新后当前数据快照: {len(snapshot_rows)} 条候选比赛")
            bind_selected_matches_to_feed(selected, snapshot_rows, logger)
        except Exception as exc:
            logger.log(f"比赛数据绑定失败: {exc}", "WARN")

        if explicit_selected:
            selected = annotate_selected_matches_for_recording(selected)
        elif getattr(args, "allow_unbound", False):
            selected = annotate_selected_matches_for_recording(selected)
            bound_count = sum(1 for match in selected if match.get("data_binding_status") == "bound")
            unbound_count = len(selected) - bound_count
            logger.log(
                f"直连测试链最佳努力绑定: bound={bound_count} | unbound={unbound_count} | total={len(selected)}"
            )
            if args.max_streams > 0:
                selected = prioritize_selected_matches(selected, args.max_streams, logger)
                logger.log(f"直连测试链最终录制 {len(selected)} 场")
        else:
            selected = require_bound_data_matches(selected, args.max_streams, logger)
            if not selected:
                return [], (cookie, template, use_dashboard, feed_url, data_source)
            selected = prioritize_selected_matches(selected, args.max_streams, logger)
            logger.log(f"正式直连链最终录制 {len(selected)} 场")

    selected = filter_matches_ready_to_record(selected, args.prestart_minutes, logger)
    return selected, (cookie, template, use_dashboard, feed_url, data_source)
