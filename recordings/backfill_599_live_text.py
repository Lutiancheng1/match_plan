#!/usr/bin/env python3
"""Backfill 599 live text data into old recording sessions.

For each recorded match that lacks 599 data, this script:
1. Reads team names from betting_data/timeline to identify the match
2. Queries 599 finished matches API by date to find the thirdId
3. Fetches full live text via get_all_live_text()
4. Calculates video alignment (kickoff offset) using timeline timestamps
5. Writes {prefix}__live_events.jsonl into the match directory

Usage:
    python3 backfill_599_live_text.py [--session-dir DIR] [--dry-run]
    python3 backfill_599_live_text.py --all   # scan all sessions
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import re
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))
sys.path.insert(0, str(SCRIPT_DIR / "pion_gst_direct_chain"))

from api_599_client import api_request, get_all_live_text, get_match_info  # noqa: E402

SESSIONS_DIR = Path(os.path.expanduser("~/Desktop/recordings/sessions"))
TZ_599 = timezone(timedelta(hours=8))  # 599 uses Beijing time


def _log(msg: str) -> None:
    print(f"[backfill_599] {msg}", flush=True)


# ---------------------------------------------------------------------------
# 599 finished match catalog
# ---------------------------------------------------------------------------

_finished_cache: dict[str, list[dict]] = {}


def fetch_finished_catalog(date_str: str) -> list[dict]:
    """Fetch 599 finished matches for a given date (YYYY-MM-DD, Beijing time).

    Returns list of dicts with: thirdId, home, away, league, date, time.
    """
    if date_str in _finished_cache:
        return _finished_cache[date_str]

    r = api_request("/footballapi/core/matchlist/v1/result", {"date": date_str})
    raw_matches = r.get("current", {}).get("match", [])
    catalog: list[dict] = []
    for m in raw_matches:
        if not isinstance(m, list) or len(m) < 40:
            continue
        catalog.append({
            "thirdId": str(m[1]),
            "home": str(m[4]),
            "away": str(m[7]),
            "date": str(m[9]),
            "time": str(m[10]),
            "league": str(m[39]),
        })
    _finished_cache[date_str] = catalog
    _log(f"599 finished catalog {date_str}: {len(catalog)} matches")
    return catalog


def _normalize(text: str) -> str:
    """Lowercase, strip whitespace/parens markers for fuzzy matching."""
    text = re.sub(r"\(.*?\)", "", text)  # remove (中) etc.
    text = re.sub(r"[^\w\u4e00-\u9fff]", "", text.lower())
    return text.strip()


def _char_overlap_ratio(a: str, b: str) -> float:
    """Character-level overlap ratio for Chinese name fuzzy matching."""
    if not a or not b:
        return 0.0
    set_a, set_b = set(a), set(b)
    overlap = len(set_a & set_b)
    return overlap / min(len(set_a), len(set_b))


def _team_match(our_name: str, their_name: str) -> bool:
    """Fuzzy team name match — substring, alias, or character overlap."""
    a, b = _normalize(our_name), _normalize(their_name)
    if not a or not b:
        return False
    # Exact or substring
    if a in b or b in a or a == b:
        return True
    # Character overlap for Chinese names (handles translation differences)
    if _char_overlap_ratio(a, b) >= 0.5 and min(len(a), len(b)) >= 2:
        return True
    # Try team aliases from run_auto_capture
    try:
        from run_auto_capture import get_team_aliases, normalize_match_text, same_match_text
        if same_match_text(our_name, their_name):
            return True
        aliases = get_team_aliases(our_name)
        target = normalize_match_text(their_name)
        if target and any(al and (al == target or al in target or target in al) for al in aliases):
            return True
    except Exception:
        pass
    return False


def find_599_match(
    team_h: str,
    team_c: str,
    league: str,
    match_utc: datetime | None,
) -> dict | None:
    """Find the 599 thirdId for a match by team name + date matching."""
    if not team_h or not team_c:
        return None

    # Determine which dates to search (Beijing time)
    dates_to_check: list[str] = []
    if match_utc:
        beijing_dt = match_utc.astimezone(TZ_599)
        base_date = beijing_dt.strftime("%Y-%m-%d")
        dates_to_check.append(base_date)
        # Also check adjacent day (matches near midnight)
        prev_date = (beijing_dt - timedelta(days=1)).strftime("%Y-%m-%d")
        next_date = (beijing_dt + timedelta(days=1)).strftime("%Y-%m-%d")
        dates_to_check.extend([prev_date, next_date])
    else:
        # Fallback: try a range of dates
        return None

    best_match = None
    best_score = 0

    for date_str in dates_to_check:
        catalog = fetch_finished_catalog(date_str)
        for entry in catalog:
            score = 0
            # Home/away matching
            home_hit = _team_match(team_h, entry["home"])
            away_hit = _team_match(team_c, entry["away"])
            rev_home_hit = _team_match(team_h, entry["away"])
            rev_away_hit = _team_match(team_c, entry["home"])

            if home_hit:
                score += 50
            if away_hit:
                score += 50
            # Reversed home/away
            if score < 50:
                if rev_home_hit:
                    score += 40
                if rev_away_hit:
                    score += 40

            # At least one team must match
            if score < 40:
                continue

            # League similarity bonus
            if league and entry["league"]:
                if _team_match(league, entry["league"]):
                    score += 20

            # Time proximity bonus (if only one team matched, time helps confirm)
            if match_utc and entry["date"] and entry["time"]:
                try:
                    entry_dt_str = f"{entry['date']} {entry['time']}"
                    entry_dt = datetime.strptime(entry_dt_str, "%Y-%m-%d %H:%M").replace(tzinfo=TZ_599)
                    diff_hours = abs((match_utc - entry_dt).total_seconds()) / 3600
                    if diff_hours <= 2:
                        score += 15
                    elif diff_hours <= 6:
                        score += 5
                except Exception:
                    pass

            if score >= 50 and score > best_score:
                best_score = score
                best_match = entry

    return best_match


# ---------------------------------------------------------------------------
# Session scanning
# ---------------------------------------------------------------------------

def scan_match_dir(match_dir: Path) -> dict | None:
    """Extract metadata from a match recording directory.

    Returns dict with team names, timestamps, file paths, or None if unusable.
    """
    # Already has 599 data?
    if list(match_dir.glob("*__live_events.jsonl")):
        return None

    # Find betting_data or timeline for team names
    bd_files = list(match_dir.glob("*betting_data.jsonl"))
    tl_files = list(match_dir.glob("*timeline.csv"))

    team_h = team_c = league = ""
    first_utc: datetime | None = None
    file_prefix = ""

    # Extract from timeline.csv (has UTC timestamp + team names)
    if tl_files:
        tl_path = tl_files[0]
        file_prefix = tl_path.name.replace("__timeline.csv", "")
        try:
            with open(tl_path, newline="", encoding="utf-8") as f:
                reader = csv.DictReader(f)
                for row in reader:
                    if not team_h:
                        team_h = row.get("team_h", "")
                        team_c = row.get("team_c", "")
                        league = row.get("league", "")
                    ts_str = row.get("timestamp_utc", "")
                    if ts_str and not first_utc:
                        try:
                            first_utc = datetime.fromisoformat(ts_str)
                        except ValueError:
                            pass
                    if team_h and first_utc:
                        break
        except Exception:
            pass

    # Fallback: extract from betting_data.jsonl
    if not team_h and bd_files:
        bd_path = bd_files[0]
        file_prefix = bd_path.name.replace("__betting_data.jsonl", "")
        try:
            with open(bd_path, encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    row = json.loads(line)
                    team_h = row.get("team_h", "")
                    team_c = row.get("team_c", "")
                    league = (row.get("fields") or {}).get("LEAGUE", "")
                    ts = row.get("ts")
                    if ts and not first_utc:
                        try:
                            first_utc = datetime.fromtimestamp(float(ts), tz=timezone.utc)
                        except (ValueError, TypeError):
                            pass
                    if team_h:
                        break
        except Exception:
            pass

    if not team_h or not team_c:
        return None

    # Determine file prefix from existing files
    if not file_prefix:
        for f in match_dir.iterdir():
            if "__full.mp4" in f.name:
                file_prefix = f.name.replace("__full.mp4", "")
                break

    return {
        "match_dir": match_dir,
        "team_h": team_h,
        "team_c": team_c,
        "league": league,
        "first_utc": first_utc,
        "file_prefix": file_prefix,
    }


def find_match_dirs_in_session(session_dir: Path) -> list[Path]:
    """Find all match subdirectories in a session."""
    dirs = []
    for d in sorted(session_dir.iterdir()):
        if not d.is_dir():
            continue
        # Match dirs typically have video or timeline files
        has_video = bool(list(d.glob("*full.mp4")) or list(d.glob("seg_*.mp4")) or list(d.glob("*__seg_*.mp4")))
        has_data = bool(list(d.glob("*timeline.csv")) or list(d.glob("*betting_data.jsonl")))
        if has_video or has_data:
            dirs.append(d)
    return dirs


# ---------------------------------------------------------------------------
# 599 live text → video alignment
# ---------------------------------------------------------------------------

CODE_KICKOFF_1H = 10
CODE_KICKOFF_2H = 13


def compute_kickoff_from_events(events: list[dict]) -> int | None:
    """Find the kickoff event time_ms from 599 events.

    Returns the time field (ms since midnight) of the first kickoff event.
    """
    # Events are ordered newest-first from the API
    for event in reversed(events):
        code = event.get("code")
        if code in (CODE_KICKOFF_1H, 3):  # 3 = another kickoff variant
            return int(event.get("time", 0))
    return None


def annotate_events_with_video_pos(
    events: list[dict],
    video_start_utc: datetime,
    kickoff_time_ms: int | None,
) -> list[dict]:
    """Add _video_pos_sec to each event based on video start time.

    If kickoff event is found, use it as anchor:
        kickoff_utc = video_start_utc + kickoff_video_offset
        event_utc = kickoff_utc + (event_time - kickoff_time) / 1000
        _video_pos_sec = (event_utc - video_start_utc).total_seconds()

    Since we don't know exact kickoff offset in old videos, we estimate
    from the first timeline row (which is when the recording started).
    """
    if not events or not video_start_utc:
        return events

    annotated = []
    # Sort events by time ascending for annotation
    sorted_events = sorted(events, key=lambda e: int(e.get("time", 0)))

    if kickoff_time_ms is not None and kickoff_time_ms > 0:
        # Use kickoff as reference point
        # For old recordings without explicit kickoff_video_offset,
        # assume video started around the match start
        for event in sorted_events:
            event_time_ms = int(event.get("time", 0))
            # match_time = time elapsed since kickoff
            match_time_ms = event_time_ms - kickoff_time_ms
            # Estimate video position (match_time relative to video start)
            # We don't know exact offset, so store match_time for now
            event["_match_time_ms"] = match_time_ms
            event["_video_pos_sec"] = None  # will be filled if we can calibrate
            event["_599_source"] = "backfill"
            annotated.append(event)
    else:
        for event in sorted_events:
            event["_match_time_ms"] = None
            event["_video_pos_sec"] = None
            event["_599_source"] = "backfill"
            annotated.append(event)

    return annotated


def calibrate_video_offset(
    events: list[dict],
    timeline_path: Path | None,
) -> list[dict]:
    """Try to calibrate _video_pos_sec using timeline.csv score changes.

    Match score change timestamps in timeline with goal events in 599.
    """
    if not timeline_path or not timeline_path.exists():
        return events

    # Parse score changes from timeline
    score_changes: list[tuple[float, str]] = []
    try:
        with open(timeline_path, newline="", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            prev_score = ""
            for row in reader:
                score = f"{row.get('score_h', '')}-{row.get('score_c', '')}"
                elapsed = float(row.get("elapsed_sec", 0))
                if score != prev_score and prev_score:
                    score_changes.append((elapsed, score))
                prev_score = score
    except Exception:
        return events

    if not score_changes:
        return events

    # Find goal events in 599 (code 1029=home goal, 2053=away goal)
    goal_events = [
        e for e in events
        if e.get("code") in (1029, 2053, 1005, 2005) and e.get("_match_time_ms") is not None
    ]

    if not goal_events or not score_changes:
        return events

    # Simple calibration: match first goal time in both sources
    first_goal_match_time_sec = goal_events[0]["_match_time_ms"] / 1000.0
    first_score_change_elapsed = score_changes[0][0]

    # kickoff_video_offset = first_score_change_elapsed - first_goal_match_time_sec
    offset = first_score_change_elapsed - first_goal_match_time_sec

    _log(f"  calibration: first_goal@{first_goal_match_time_sec:.0f}s match_time, "
         f"first_score_change@{first_score_change_elapsed:.0f}s video → offset={offset:.0f}s")

    for event in events:
        mt = event.get("_match_time_ms")
        if mt is not None:
            event["_video_pos_sec"] = round(mt / 1000.0 + offset, 2)

    return events


# ---------------------------------------------------------------------------
# Write output
# ---------------------------------------------------------------------------

def write_live_events(
    match_dir: Path,
    file_prefix: str,
    events: list[dict],
    third_id: str,
    match_info: dict,
) -> Path:
    """Write live_events.jsonl into the match directory."""
    out_path = match_dir / f"{file_prefix}__live_events.jsonl"
    with open(out_path, "w", encoding="utf-8") as f:
        for event in events:
            f.write(json.dumps(event, ensure_ascii=False) + "\n")

    # Also write a summary metadata file
    meta_path = match_dir / f"{file_prefix}__live_text_599_meta.json"
    meta = {
        "thirdId": third_id,
        "total_events": len(events),
        "source": "backfill",
        "backfill_at": datetime.now(timezone.utc).isoformat(),
        "match_info": match_info,
    }
    meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")

    return out_path


# ---------------------------------------------------------------------------
# Main backfill logic
# ---------------------------------------------------------------------------

def backfill_match(match_dir: Path, dry_run: bool = False) -> bool:
    """Backfill 599 data for a single match directory. Returns True if successful."""
    meta = scan_match_dir(match_dir)
    if meta is None:
        return False

    _log(f"Processing: {meta['team_h']} vs {meta['team_c']} ({meta['league']})")

    # Find 599 match
    match_599 = find_599_match(
        meta["team_h"],
        meta["team_c"],
        meta["league"],
        meta["first_utc"],
    )
    if not match_599:
        _log(f"  ✗ No 599 match found for {meta['team_h']} vs {meta['team_c']}")
        return False

    third_id = match_599["thirdId"]
    _log(f"  ✓ Found: {match_599['home']} vs {match_599['away']} "
         f"[{match_599['league']}] thirdId={third_id}")

    if dry_run:
        _log(f"  [dry-run] Would fetch live text for thirdId={third_id}")
        return True

    # Fetch full live text
    events = get_all_live_text(third_id)
    if not events:
        _log(f"  ✗ No live text events for thirdId={third_id}")
        return False
    _log(f"  Fetched {len(events)} events")

    # Compute kickoff
    kickoff_ms = compute_kickoff_from_events(events)
    _log(f"  Kickoff time_ms: {kickoff_ms}")

    # Annotate with video positions
    events = annotate_events_with_video_pos(events, meta["first_utc"], kickoff_ms)

    # Try calibration using timeline score changes
    tl_files = list(meta["match_dir"].glob("*timeline.csv"))
    if tl_files:
        events = calibrate_video_offset(events, tl_files[0])

    # Write output
    out_path = write_live_events(
        meta["match_dir"],
        meta["file_prefix"],
        events,
        third_id,
        match_599,
    )
    _log(f"  ✓ Written: {out_path.name} ({len(events)} events)")
    return True


def backfill_session(session_dir: Path, dry_run: bool = False) -> dict:
    """Backfill all matches in a session. Returns summary."""
    match_dirs = find_match_dirs_in_session(session_dir)
    results = {"total": len(match_dirs), "success": 0, "skipped": 0, "failed": 0}

    for md in match_dirs:
        try:
            ok = backfill_match(md, dry_run=dry_run)
            if ok:
                results["success"] += 1
            else:
                results["skipped"] += 1
        except Exception as e:
            _log(f"  ✗ Error: {e}")
            results["failed"] += 1

    return results


def main():
    parser = argparse.ArgumentParser(description="Backfill 599 live text into old recordings")
    parser.add_argument("--session-dir", type=str, help="Specific session directory to backfill")
    parser.add_argument("--match-dir", type=str, help="Specific match directory to backfill")
    parser.add_argument("--all", action="store_true", help="Scan all sessions")
    parser.add_argument("--dry-run", action="store_true", help="Don't write files, just report matches")
    args = parser.parse_args()

    if args.match_dir:
        backfill_match(Path(args.match_dir), dry_run=args.dry_run)
        return

    if args.session_dir:
        session = Path(args.session_dir)
        results = backfill_session(session, dry_run=args.dry_run)
        _log(f"Session {session.name}: {results}")
        return

    if args.all:
        if not SESSIONS_DIR.exists():
            _log(f"Sessions directory not found: {SESSIONS_DIR}")
            return

        sessions = sorted(SESSIONS_DIR.iterdir())
        total_results = {"total": 0, "success": 0, "skipped": 0, "failed": 0}

        for session in sessions:
            if not session.is_dir() or not session.name.startswith("session_"):
                continue
            _log(f"\n{'='*60}")
            _log(f"Session: {session.name}")
            results = backfill_session(session, dry_run=args.dry_run)
            for k in total_results:
                total_results[k] += results[k]
            _log(f"  → {results}")

        _log(f"\n{'='*60}")
        _log(f"TOTAL: {total_results}")
        return

    parser.print_help()


if __name__ == "__main__":
    main()
