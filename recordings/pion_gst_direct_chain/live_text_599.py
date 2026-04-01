#!/usr/bin/env python3
"""599 文字直播轮询与视频对齐模块。

设计要点:
- get_live_text(startMsgId) 是向后翻旧页，不适合前向增量订阅。
  因此用 get_match_info().matchLive 做前向增量（最近~30条窗口），
  启动期用 get_live_text() 回溯历史找开球事件。
- 开球锚点：用最新事件的 match_time 反推 kickoff_utc
  （kickoff_utc = observed_at - latest_event_time_ms），
  收到 code=10 后标记为 validated。
- 比分变化时，与 betting_data 交叉验证漂移量。
"""
from __future__ import annotations

import re
import sys
import threading
import time as time_mod
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

SCRIPT_DIR = Path(__file__).resolve().parent
RECORDINGS_DIR = SCRIPT_DIR.parent
API_599_DIR = RECORDINGS_DIR.parent / "599_project"

for _p in (str(RECORDINGS_DIR), str(API_599_DIR)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from api_599 import get_live_text, get_match_info, get_match_list  # type: ignore
from run_auto_capture import (  # type: ignore
    SCHEDULE_TIMEZONE_OFFSET_HOURS,
    extract_age_markers,
    get_league_aliases,
    get_team_aliases,
    has_women_marker,
    kickoff_distance_minutes,
    normalize_match_text,
    parse_feed_datetime_minutes,
    parse_schedule_kickoff_minutes,
    same_league_text,
    same_match_text,
)

# 599 事件 code 常量
CODE_KICKOFF_1H = 10  # 上半场开球
CODE_KICKOFF_2H = 13  # 下半场开球
CODE_HALFTIME = 1     # 半场结束
CODE_FULLTIME = 20    # 终场
CODE_GOAL_HOME = 1029
CODE_GOAL_AWAY = 2053

KICKOFF_CODES = {CODE_KICKOFF_1H, 3}
GOAL_CODES = {CODE_GOAL_HOME, CODE_GOAL_AWAY, 1005, 2005}  # 含乌龙球

# 匹配候选打分阈值：低于此分认为不可信
MATCH_CONFIDENCE_THRESHOLD = 140
# 599 matchStartTime 是北京时间 (UTC+8)，赛程时间由数据站决定
_TZ_599 = timezone(timedelta(hours=8))
_TZ_SCHEDULE = timezone(timedelta(hours=SCHEDULE_TIMEZONE_OFFSET_HOURS))


# ---------------------------------------------------------------------------
# 工具函数
# ---------------------------------------------------------------------------

def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(str(value).strip()) if value is not None and value != "" else default
    except Exception:
        return default


def _coerce_utc(value: Any) -> datetime | None:
    """尝试将各种时间格式转换为 UTC datetime。"""
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc) if value.tzinfo else value.replace(tzinfo=timezone.utc)
    text = str(value).strip()
    for candidate in (text.replace("Z", "+00:00"), text):
        try:
            dt = datetime.fromisoformat(candidate)
            return dt.astimezone(timezone.utc) if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
        except Exception:
            pass
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M"):
        try:
            return datetime.strptime(text, fmt).replace(tzinfo=timezone.utc)
        except Exception:
            pass
    return None


def _parse_599_match_start_minutes(value: Any) -> int | None:
    """将 599 的 matchStartTime (北京时间) 转换到赛程时区后返回当天分钟数。"""
    text = str(value or "").strip()
    if not text:
        return None
    # 先尝试 fromisoformat（支持 T 分隔符和带偏移的 ISO 格式）
    for candidate in (text, text.replace("Z", "+00:00")):
        try:
            dt = datetime.fromisoformat(candidate)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=_TZ_599)
            local = dt.astimezone(_TZ_SCHEDULE)
            return local.hour * 60 + local.minute
        except Exception:
            pass
    # 常见非 ISO 格式
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y/%m/%d %H:%M:%S", "%Y/%m/%d %H:%M"):
        try:
            dt = datetime.strptime(text, fmt).replace(tzinfo=_TZ_599)
            local = dt.astimezone(_TZ_SCHEDULE)
            return local.hour * 60 + local.minute
        except Exception:
            pass
    return None


def _event_text(event: dict) -> str:
    return str(event.get("msgText") or event.get("content") or event.get("text") or "").strip()


def _event_time_ms(event: dict) -> int:
    return _safe_int(event.get("time"), default=-1)


def _event_msg_id(event: dict) -> str:
    msg_id = str(event.get("msgId") or "").strip()
    return msg_id or f"{_event_time_ms(event)}|{event.get('code', '')}|{_event_text(event)[:30]}"


def _event_sort_key(event: dict) -> tuple[int, int]:
    return (_event_time_ms(event), _safe_int(event.get("msgId")))


def _is_kickoff_event(event: dict) -> bool:
    code = _safe_int(event.get("code"), default=-1)
    time_ms = _event_time_ms(event)
    if code not in KICKOFF_CODES or time_ms < 0:
        return False
    return time_ms <= 120_000  # 开球事件应在前2分钟内


def _extract_score_from_text(text: str) -> str | None:
    """从文字中提取比分，如 'Goal~~~~ 0:1' → '0-1'。"""
    m = re.search(r"(?<!\d)(\d{1,2})\s*[:：-]\s*(\d{1,2})(?!\d)", text)
    return f"{int(m.group(1))}-{int(m.group(2))}" if m else None


def _log(logger: Any, msg: str, tag: str = "") -> None:
    if logger:
        try:
            logger.log(msg, tag) if tag else logger.log(msg)
        except Exception:
            pass


# ---------------------------------------------------------------------------
# AlignmentEngine: 视频-比赛时间对齐引擎
# ---------------------------------------------------------------------------

class AlignmentEngine:
    """管理 video_offset ↔ match_time 的映射关系。

    核心公式: video_pos = kickoff_video_offset + (match_time_ms / 1000)
    其中 kickoff_video_offset = kickoff_utc - video_start_utc
    """

    def __init__(self, video_start_utc: datetime | str | None = None, logger: Any = None):
        self._lock = threading.Lock()
        self.logger = logger
        self.video_start_utc: datetime | None = _coerce_utc(video_start_utc)
        self.kickoff_utc: datetime | None = None
        self.kickoff_source: str = ""         # "live_event_clock" | "kickoff_event_validated"
        self.kickoff_event: dict = {}
        self.last_match_time_ms: int | None = None
        # betting_data 比分变化记录，用于交叉校验
        self._betting_score_log: list[dict] = []
        self._last_betting_score: str = ""

    def set_video_start_utc(self, value: datetime | str | None) -> None:
        dt = _coerce_utc(value)
        if dt is None:
            return
        with self._lock:
            if self.video_start_utc is None:
                self.video_start_utc = dt

    def update_from_live_events(self, events: list[dict], observed_at: datetime | None = None) -> None:
        """用最新 599 事件的 match_time 反推 kickoff_utc。"""
        observed_at = observed_at or _now_utc()
        times = [_event_time_ms(e) for e in events if _event_time_ms(e) >= 0]
        kickoff_evt = next((e for e in events if _is_kickoff_event(e)), None)
        action = ""
        drift_sec = None
        with self._lock:
            if times:
                latest_ms = max(times)
                candidate_kickoff = observed_at - timedelta(milliseconds=latest_ms)
                if self.kickoff_utc is None:
                    self.kickoff_utc = candidate_kickoff
                    self.kickoff_source = "live_event_clock"
                    action = "init"
                else:
                    # 只在偏差不大时更新（渐进修正）
                    drift_sec = abs((candidate_kickoff - self.kickoff_utc).total_seconds())
                    if drift_sec <= 30:
                        self.kickoff_utc = candidate_kickoff
                        action = "refine"
                    else:
                        action = "skip_large_drift"
                self.last_match_time_ms = latest_ms
            if kickoff_evt and not self.kickoff_event:
                self.kickoff_event = {
                    "msgId": _event_msg_id(kickoff_evt),
                    "time_ms": _event_time_ms(kickoff_evt),
                    "text": _event_text(kickoff_evt),
                }
                if self.kickoff_source == "live_event_clock":
                    self.kickoff_source = "kickoff_event_validated"
        # 日志
        if action and times:
            drift_text = f" drift={drift_sec:.1f}s" if drift_sec is not None else ""
            _log(self.logger, f"599 kickoff推算: events={len(events)} latest={max(times)/1000:.0f}s action={action}{drift_text} source={self.kickoff_source}")
        if kickoff_evt and self.kickoff_event:
            _log(self.logger, f"599 kickoff校验: {_event_text(kickoff_evt)[:80]}")

    def observe_betting_score(self, rows: list[dict]) -> None:
        """记录 betting_data 中的比分变化，供后续交叉校验。"""
        with self._lock:
            for row in rows:
                sh = str(row.get("score_h") or row.get("fields", {}).get("SCORE_H", "")).strip()
                sc = str(row.get("score_c") or row.get("fields", {}).get("SCORE_C", "")).strip()
                if not sh.isdigit() or not sc.isdigit():
                    continue
                score = f"{sh}-{sc}"
                if score == self._last_betting_score:
                    continue
                ts = _coerce_utc(row.get("timestamp"))
                if ts:
                    self._betting_score_log.append({"score": score, "utc": ts})
                    self._last_betting_score = score

    def kickoff_video_offset(self) -> float | None:
        """返回开球在视频中的偏移量（秒）。"""
        with self._lock:
            if self.kickoff_utc is None or self.video_start_utc is None:
                return None
            return (self.kickoff_utc - self.video_start_utc).total_seconds()

    def match_time_to_video(self, time_ms: int) -> float | None:
        """将比赛内时间(ms)转换为视频偏移(秒)。"""
        offset = self.kickoff_video_offset()
        return (offset + time_ms / 1000.0) if offset is not None else None

    def validate_score_drift(self, score: str, match_time_ms: int) -> dict | None:
        """比分变化交叉校验：计算 599 事件时间与 betting_data 同一比分的时间差。"""
        event_wallclock = None
        with self._lock:
            if self.kickoff_utc:
                event_wallclock = self.kickoff_utc + timedelta(milliseconds=match_time_ms)
        if not event_wallclock or not score:
            return None
        with self._lock:
            for entry in self._betting_score_log:
                if entry["score"] == score:
                    delta = (entry["utc"] - event_wallclock).total_seconds()
                    return {"score": score, "drift_sec": round(delta, 1), "betting_utc": entry["utc"].isoformat()}
        return None

    def annotate_event(self, event: dict) -> dict:
        """为 599 事件附加视频位置和对齐元数据。"""
        time_ms = _event_time_ms(event)
        code = _safe_int(event.get("code"), default=-1)
        video_pos = self.match_time_to_video(time_ms) if time_ms >= 0 else None
        row = dict(event)
        row["_video_pos_sec"] = round(video_pos, 3) if video_pos is not None else None
        row["_match_time_ms"] = time_ms if time_ms >= 0 else None
        # 进球事件附加比分漂移交叉校验
        score_text = _extract_score_from_text(_event_text(event))
        if score_text and time_ms >= 0 and code in GOAL_CODES:
            drift = self.validate_score_drift(score_text, time_ms)
            if drift:
                row["_score_drift"] = drift
                _log(self.logger, f"599 比分校验: {score_text} match={time_ms/1000:.0f}s drift={drift['drift_sec']}s")
            else:
                _log(self.logger, f"599 比分校验: {score_text} match={time_ms/1000:.0f}s betting未找到对应比分")
        return row

    def snapshot(self) -> dict:
        ko = self.kickoff_video_offset()
        with self._lock:
            return {
                "kickoffUtc": self.kickoff_utc.isoformat() if self.kickoff_utc else "",
                "kickoffSource": self.kickoff_source,
                "kickoffVideoOffsetSec": round(ko, 3) if ko is not None else None,
                "videoStartUtc": self.video_start_utc.isoformat() if self.video_start_utc else "",
                "lastMatchTimeMs": self.last_match_time_ms,
            }


# ---------------------------------------------------------------------------
# LiveTextPoller599: 599 文字直播轮询器
# ---------------------------------------------------------------------------

class LiveTextPoller599:
    """轮询 599 API 获取实时文字直播，并将事件注入 AlignmentEngine。

    使用方式:
        poller = LiveTextPoller599("England", "Japan", ...)
        threading.Thread(target=poller.start, daemon=True).start()
        # 定期调用 drain_pending() 获取新事件并落盘
    """

    def __init__(
        self,
        team_h: str,
        team_c: str,
        *,
        selected_match: dict | None = None,
        league: str = "",
        alignment: AlignmentEngine | None = None,
        poll_interval: float = 12.0,
        backfill_pages: int = 12,
        logger: Any = None,
    ):
        self.team_h = (team_h or "").strip()
        self.team_c = (team_c or "").strip()
        self.selected_match = dict(selected_match or {})
        self.league = league or str(self.selected_match.get("league", ""))
        self.alignment = alignment or AlignmentEngine()
        self.poll_interval = max(5.0, float(poll_interval))
        self.backfill_pages = int(backfill_pages)
        self.logger = logger
        # 确保 alignment engine 也有 logger
        if logger and not getattr(self.alignment, "logger", None):
            self.alignment.logger = logger

        # 状态
        self.third_id: str = ""
        self.resolved_home: str = ""
        self.resolved_away: str = ""
        self.matched_by: str = ""
        self.state: str = "idle"
        self.poll_count: int = 0
        self.error_count: int = 0
        self.last_error: str = ""

        # 事件存储
        self.data: list[dict] = []
        self._pending: list[dict] = []
        self._seen_ids: set[str] = set()
        self._lock = threading.Lock()
        self._stop = threading.Event()

    def stop(self) -> None:
        self._stop.set()

    def drain_pending(self) -> list[dict]:
        """取出自上次调用以来的新事件（线程安全）。"""
        with self._lock:
            rows = list(self._pending)
            self._pending.clear()
            return rows

    def snapshot(self) -> dict:
        with self._lock:
            return {
                "state": self.state,
                "thirdId": self.third_id,
                "matchedBy": self.matched_by,
                "resolvedHome": self.resolved_home,
                "resolvedAway": self.resolved_away,
                "eventCount": len(self.data),
                "pollCount": self.poll_count,
                "errorCount": self.error_count,
                "lastError": self.last_error,
            }

    # ---- 主循环 ----

    def start(self) -> None:
        """阻塞式主循环，应在独立线程中运行。"""
        while not self._stop.is_set():
            try:
                if not self.third_id:
                    self.state = "resolving"
                    if not self._resolve_match():
                        self._stop.wait(timeout=max(15.0, self.poll_interval))
                        continue
                    self._backfill_history()
                self.state = "polling"
                self._poll_once()
            except Exception as exc:
                self.error_count += 1
                self.last_error = str(exc)
                self.state = "error"
                _log(self.logger, f"599 轮询异常: {exc}", "WARN")
            self._stop.wait(timeout=self.poll_interval)

    # ---- 比赛匹配 ----

    def _resolve_match(self) -> bool:
        """通过 599 match_list 查找对应 thirdId。"""
        result = get_match_list()
        groups = result.get("all", [])
        if not isinstance(groups, list):
            self.last_error = "599 match_list 格式异常"
            _log(self.logger, self.last_error, "WARN")
            return False

        # 优先同联赛候选
        league_hints: list[str] = []
        for candidate in (
            self.league,
            self.selected_match.get("data_league", ""),
            (self.selected_match.get("_feed_binding") or {}).get("league", ""),
        ):
            text = str(candidate or "").strip()
            if text and text not in league_hints:
                league_hints.append(text)
        preferred, fallback = [], []
        for g in groups:
            if not isinstance(g, dict):
                continue
            race = str(g.get("racename", "")).strip()
            ids = [str(i).strip() for i in (g.get("thirdId") or []) if str(i).strip()]
            bucket = preferred if any(same_league_text(hint, race) for hint in league_hints) else fallback
            bucket.extend((tid, race) for tid in ids)

        # 不再截断候选列表，避免真实比赛排位靠后时被漏掉。
        candidates = preferred + fallback
        best_score, best_tid, best_reason, best_home, best_away = -1, "", "", "", ""

        for tid, race in candidates:
            try:
                info = get_match_info(tid)
                detail = info.get("data", {})
                if not isinstance(detail, dict):
                    continue
                score, reason = self._score_candidate(detail, race)
                if score > best_score:
                    best_score, best_tid, best_reason = score, tid, reason
                    best_home = str((detail.get("homeTeamInfo") or {}).get("name", "")).strip()
                    best_away = str((detail.get("guestTeamInfo") or {}).get("name", "")).strip()
                time_mod.sleep(0.05)  # 轻微限速
            except Exception:
                continue

        if best_score < MATCH_CONFIDENCE_THRESHOLD:
            self.last_error = f"未找到可信匹配 (best={best_score})"
            _log(self.logger, f"599 匹配失败: {self.team_h} vs {self.team_c} | best={best_home} vs {best_away} | {best_reason} | score={best_score}", "WARN")
            return False

        self.third_id = best_tid
        self.resolved_home = best_home
        self.resolved_away = best_away
        self.matched_by = best_reason
        self.last_error = ""
        _log(self.logger, f"599 匹配成功: {best_tid} | {best_home} vs {best_away} | {best_reason} | score={best_score}")
        return True

    def _score_candidate(self, detail: dict, race_from_group: str) -> tuple[int, str]:
        """对一个 599 比赛候选打分，越高越匹配。"""
        home = str((detail.get("homeTeamInfo") or {}).get("name", "")).strip()
        away = str((detail.get("guestTeamInfo") or {}).get("name", "")).strip()
        race = str(detail.get("matchType1") or detail.get("raceName") or race_from_group or "").strip()

        score = 0
        reasons = []

        home_hints = self._team_hints("home")
        away_hints = self._team_hints("away")
        league_hints = self._league_hints()

        # 球队名匹配（最重要）
        if any(hint and same_match_text(hint, home) for hint in home_hints):
            score += 95
            reasons.append("home_match")
        elif any(hint and self._alias_hit(hint, home) for hint in home_hints):
            score += 80
            reasons.append("home_alias")

        if any(hint and same_match_text(hint, away) for hint in away_hints):
            score += 95
            reasons.append("away_match")
        elif any(hint and self._alias_hit(hint, away) for hint in away_hints):
            score += 80
            reasons.append("away_alias")

        # 联赛匹配
        if race and any(hint and same_league_text(hint, race) for hint in league_hints):
            score += 70
            reasons.append("league")

        # 年龄段/性别不匹配惩罚
        our_markers = extract_age_markers(self.team_h) | extract_age_markers(self.team_c)
        their_markers = extract_age_markers(home) | extract_age_markers(away) | extract_age_markers(race)
        our_is_women = any(has_women_marker(text) for text in (self.team_h, self.team_c, self.league))
        their_is_women = any(has_women_marker(text) for text in (home, away, race))
        if our_markers != their_markers:
            score -= 120
            reasons.append(f"age_mismatch({our_markers}vs{their_markers})")
        if our_is_women != their_is_women:
            score -= 120
            reasons.append("gender_mismatch")

        # 开赛时间距离（599 是北京时间，需转换到赛程时区）
        sched_min = parse_schedule_kickoff_minutes(self.selected_match)
        match_start = str(detail.get("matchStartTime") or "")
        feed_min = _parse_599_match_start_minutes(match_start)
        dist = kickoff_distance_minutes(sched_min, feed_min)
        if dist is not None:
            if dist <= 5:
                score += 50
                reasons.append(f"time<={dist}m")
            elif dist <= 15:
                score += 25
                reasons.append(f"time<={dist}m")
            else:
                score -= min(30, dist // 5)
                reasons.append(f"time_far={dist}m")

        return score, "+".join(reasons)

    def _team_hints(self, side: str) -> list[str]:
        hints: list[str] = []
        keys = ["team_h", "data_team_h", "team_h"] if side == "home" else ["team_c", "data_team_c", "team_c"]
        feed_key = "team_h" if side == "home" else "team_c"
        values = [
            self.team_h if side == "home" else self.team_c,
            self.selected_match.get(keys[1], ""),
            (self.selected_match.get("_feed_binding") or {}).get(feed_key, ""),
        ]
        for value in values:
            text = str(value or "").strip()
            if text and text not in hints:
                hints.append(text)
        return hints

    def _league_hints(self) -> list[str]:
        hints: list[str] = []
        for value in (
            self.league,
            self.selected_match.get("data_league", ""),
            (self.selected_match.get("_feed_binding") or {}).get("league", ""),
        ):
            text = str(value or "").strip()
            if text and text not in hints:
                hints.append(text)
        return hints

    @staticmethod
    def _alias_hit(our_name: str, their_name: str) -> bool:
        aliases = get_team_aliases(our_name)
        target = normalize_match_text(their_name)
        if not target:
            return False
        return any(a and (a == target or a in target or target in a) for a in aliases)

    # ---- 数据拉取 ----

    def _backfill_history(self) -> None:
        """启动时回溯历史事件，直到找到开球事件或达到页数限制。"""
        info = get_match_info(self.third_id)
        window = self._extract_live_window(info)
        observed_at = _now_utc()
        self.alignment.update_from_live_events(window, observed_at)

        collected = list(window)
        found_kickoff = any(_is_kickoff_event(e) for e in window)
        last_msg_id = str(window[-1].get("msgId", "")).strip() if window else ""
        total = len(window)

        page = 0
        while last_msg_id and not found_kickoff and page < self.backfill_pages:
            resp = get_live_text(self.third_id, last_msg_id, str(total))
            rows = resp.get("data", [])
            if not isinstance(rows, list) or not rows:
                break
            collected.extend(rows)
            found_kickoff = any(_is_kickoff_event(e) for e in rows)
            last_msg_id = str(rows[-1].get("msgId", "")).strip()
            total += len(rows)
            page += 1

        ingested = self._ingest(collected, observed_at, source="backfill")
        snap = self.alignment.snapshot()
        _log(self.logger, f"599 历史回溯: collected={len(collected)} ingested={ingested} kickoff={'found' if found_kickoff else 'not_found'} offset={snap.get('kickoffVideoOffsetSec')}s")

    def _poll_once(self) -> None:
        """单次轮询：拉取最新 matchLive 窗口。"""
        info = get_match_info(self.third_id)
        window = self._extract_live_window(info)
        observed_at = _now_utc()
        self.alignment.update_from_live_events(window, observed_at)
        ingested = self._ingest(window, observed_at, source="poll")
        self.poll_count += 1
        with self._lock:
            total = len(self.data)
        _log(self.logger, f"599 轮询#{self.poll_count}: window={len(window)} new={ingested} total={total}")

    @staticmethod
    def _extract_live_window(payload: dict) -> list[dict]:
        data = payload.get("data", {})
        if not isinstance(data, dict):
            return []
        mi = data.get("matchInfo", {})
        if not isinstance(mi, dict):
            return []
        rows = mi.get("matchLive", [])
        return [r for r in rows if isinstance(r, dict)]

    def _ingest(self, rows: list[dict], observed_at: datetime, source: str) -> int:
        new_rows = []
        for row in sorted(rows, key=_event_sort_key):
            mid = _event_msg_id(row)
            if mid in self._seen_ids:
                continue
            self._seen_ids.add(mid)
            item = dict(row)
            item["_599_observed_at"] = observed_at.isoformat()
            item["_599_source"] = source
            new_rows.append(item)
        if new_rows:
            with self._lock:
                self.data.extend(new_rows)
                self._pending.extend(new_rows)
        return len(new_rows)
