#!/usr/bin/env python3
"""Render sync viewer HTML into a side-by-side analysis video.

This script opens a generated *__sync_viewer.html page in a desktop browser,
starts playback, and captures the browser content area as a standalone
analysis video. The resulting MP4 contains the left-side match video and the
right-side live data panel exactly as shown in the sync viewer.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

from recorder import WindowCaptureProcess
from run_auto_capture import (
    applescript_quote,
    compute_page_content_rect,
    get_all_browser_window_ids,
    get_browser_app,
    get_watch_playback_state,
)


DEFAULT_BROWSER = "safari"
DEFAULT_WIDTH = 1760
DEFAULT_HEIGHT = 1080
DEFAULT_LEFT = 80
DEFAULT_TOP = 70
OPEN_WAIT_SECONDS = 15.0
PLAYBACK_WAIT_SECONDS = 20.0
TAIL_PADDING_SECONDS = 0.8


def run_osascript(script: str, timeout: int = 20) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["osascript", "-e", script],
        capture_output=True,
        text=True,
        timeout=timeout,
    )


def list_tabs(browser: str) -> list[dict]:
    app = get_browser_app(browser)
    title_expr = "name of t" if browser == "safari" else "title of t"
    script = "\n".join(
        [
            f'tell application "{app}"',
            '    set out to ""',
            '    repeat with wIndex from 1 to (count windows)',
            '        repeat with tIndex from 1 to (count tabs of window wIndex)',
            '            set t to tab tIndex of window wIndex',
            '            try',
            '                set u to URL of t',
            f'                set ttl to {title_expr}',
            '                set out to out & (wIndex as text) & "|" & (tIndex as text) & "|" & u & "|" & ttl & linefeed',
            '            end try',
            '        end repeat',
            '    end repeat',
            '    return out',
            'end tell',
        ]
    )
    result = run_osascript(script, timeout=20)
    tabs = []
    for line in (result.stdout or "").splitlines():
        parts = line.split("|", 3)
        if len(parts) != 4:
            continue
        try:
            window_index = int(parts[0])
            tab_index = int(parts[1])
        except ValueError:
            continue
        tabs.append(
            {
                "window_index": window_index,
                "tab_index": tab_index,
                "url": parts[2].strip(),
                "title": parts[3].strip(),
            }
        )
    return tabs


def find_tab_for_url(browser: str, url: str) -> dict | None:
    normalized = url.rstrip("/")
    for tab in list_tabs(browser):
        if tab["url"].rstrip("/") == normalized:
            return tab
    return None


def open_viewer_window(browser: str, url: str, left: int, top: int, width: int, height: int) -> tuple[dict | None, int | None]:
    app = get_browser_app(browser)
    escaped = applescript_quote(url)
    before = set(get_all_browser_window_ids(browser))
    right = left + width
    bottom = top + height
    if browser == "safari":
        script = f'''
            tell application "{app}"
                activate
                make new document with properties {{URL:"{escaped}"}}
                delay 0.6
                set bounds of front window to {{{left}, {top}, {right}, {bottom}}}
            end tell
        '''
    else:
        script = f'''
            tell application "{app}"
                activate
                set newWindow to make new window
                set bounds of front window to {{{left}, {top}, {right}, {bottom}}}
                set URL of active tab of newWindow to "{escaped}"
            end tell
        '''
    result = run_osascript(script, timeout=25)
    if result.returncode != 0:
        raise RuntimeError((result.stderr or result.stdout).strip() or "open viewer window failed")

    deadline = time.time() + OPEN_WAIT_SECONDS
    tab = None
    while time.time() < deadline:
        tab = find_tab_for_url(browser, url)
        after = set(get_all_browser_window_ids(browser))
        new_ids = list(after - before)
        if tab and new_ids:
            return tab, new_ids[0]
        time.sleep(0.5)
    after = set(get_all_browser_window_ids(browser))
    new_ids = list(after - before)
    return tab, (new_ids[0] if new_ids else None)


def execute_js(browser: str, window_index: int, tab_index: int, js: str) -> str:
    app = get_browser_app(browser)
    escaped = applescript_quote(js)
    if browser == "safari":
        script = f'''
            tell application "{app}"
                set js to "{escaped}"
                return do JavaScript js in tab {tab_index} of window {window_index}
            end tell
        '''
    else:
        script = f'''
            tell application "{app}"
                set js to "{escaped}"
                return execute tab {tab_index} of window {window_index} javascript js
            end tell
        '''
    result = run_osascript(script, timeout=20)
    return (result.stdout or "").strip()


def close_window(browser: str, window_index: int) -> None:
    app = get_browser_app(browser)
    script = "\n".join(
        [
            f'tell application "{app}"',
            f"    try",
            f"        close window {window_index}",
            f"    end try",
            "end tell",
        ]
    )
    try:
        run_osascript(script, timeout=10)
    except Exception:
        pass


def play_viewer(browser: str, tab: dict) -> None:
    js = """
(() => {
  const v = document.querySelector('video');
  if (!v) return 'NO_VIDEO';
  v.muted = true;
  v.currentTime = 0;
  try { v.pause(); } catch (e) {}
  const p = v.play();
  if (p && typeof p.then === 'function') {
    p.then(() => {}).catch(() => {});
  }
  return 'OK';
})()
""".strip()
    execute_js(browser, tab["window_index"], tab["tab_index"], js)


def fetch_duration(browser: str, tab: dict) -> float:
    js = """
(() => {
  const v = document.querySelector('video');
  return v ? String(v.duration || 0) : '0';
})()
""".strip()
    raw = execute_js(browser, tab["window_index"], tab["tab_index"], js)
    try:
        return float(raw or 0)
    except ValueError:
        return 0.0


def wait_for_playback(browser: str, tab: dict, timeout: float) -> dict:
    deadline = time.time() + timeout
    last_state = None
    while time.time() < deadline:
        state = get_watch_playback_state(tab["window_index"], tab["tab_index"], browser)
        if state:
            last_state = state
            videos = state.get("videos") or []
            for video in videos:
                if (
                    not video.get("paused", True)
                    and not video.get("ended", False)
                    and float(video.get("currentTime", 0) or 0) >= 0
                    and int(video.get("readyState", 0) or 0) >= 2
                ):
                    return state
        time.sleep(0.5)
    if last_state:
        return last_state
    raise RuntimeError("viewer playback did not become ready in time")


def capture_viewer(html_path: Path, browser: str, fps: int, left: int, top: int, width: int, height: int, output_path: Path) -> Path:
    url = html_path.resolve().as_uri()
    tab, window_id = open_viewer_window(browser, url, left, top, width, height)
    if not tab or not window_id:
        raise RuntimeError(f"unable to open viewer window for {html_path}")
    try:
        play_viewer(browser, tab)
        state = wait_for_playback(browser, tab, PLAYBACK_WAIT_SECONDS)
        duration = fetch_duration(browser, tab)
        crop = compute_page_content_rect(state) or {}
        recorder = WindowCaptureProcess(
            window_id=window_id,
            output_path=str(output_path),
            fps=fps,
            width=0,
            height=0,
            content_crop=crop,
        )
        process = recorder.start()
        if not process:
            raise RuntimeError("window capture helper failed to start")
        start_ts = time.time()
        deadline = start_ts + max(1.0, duration) + TAIL_PADDING_SECONDS
        while time.time() < deadline:
            state = get_watch_playback_state(tab["window_index"], tab["tab_index"], browser)
            if state:
                videos = state.get("videos") or []
                if videos and any(v.get("ended") for v in videos):
                    break
            time.sleep(0.5)
        recorder.stop()
        return output_path
    finally:
        close_window(browser, tab["window_index"])


def discover_html_targets(input_path: Path) -> list[Path]:
    if input_path.is_file():
        return [input_path]
    return sorted(input_path.rglob("*__sync_viewer.html"))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Render sync viewer HTML into side-by-side analysis video")
    parser.add_argument("input", help="Session directory or *__sync_viewer.html file")
    parser.add_argument("--browser", default=DEFAULT_BROWSER, choices=["chrome", "safari"])
    parser.add_argument("--fps", type=int, default=30)
    parser.add_argument("--left", type=int, default=DEFAULT_LEFT)
    parser.add_argument("--top", type=int, default=DEFAULT_TOP)
    parser.add_argument("--width", type=int, default=DEFAULT_WIDTH)
    parser.add_argument("--height", type=int, default=DEFAULT_HEIGHT)
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--first-only", action="store_true")
    args = parser.parse_args(argv)

    input_path = Path(args.input).expanduser().resolve()
    targets = discover_html_targets(input_path)
    if args.first_only and targets:
        targets = targets[:1]
    if not targets:
        raise SystemExit(f"未找到 sync viewer HTML: {input_path}")

    outputs = []
    for html_path in targets:
        output_path = html_path.with_name(html_path.stem.replace("__sync_viewer", "__analysis_side_by_side") + ".mp4")
        if output_path.exists() and not args.force:
            print(json.dumps({"html": str(html_path), "output": str(output_path), "status": "exists"}, ensure_ascii=False))
            outputs.append(output_path)
            continue
        rendered = capture_viewer(
            html_path=html_path,
            browser=args.browser,
            fps=args.fps,
            left=args.left,
            top=args.top,
            width=args.width,
            height=args.height,
            output_path=output_path,
        )
        print(json.dumps({"html": str(html_path), "output": str(rendered), "status": "rendered"}, ensure_ascii=False))
        outputs.append(rendered)

    print(json.dumps({"rendered": [str(p) for p in outputs]}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
