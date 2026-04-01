#!/usr/bin/env python3
"""POC 测试: 验证 599 文字直播 → 视频对齐精度。

用昨天 England vs Japan 的已有录制数据 + 599 API 做端到端验证。
已知事实（从视频帧目视确认）:
- video offset 600s  → 记分牌 4:15  (match_time=255s)
- video offset 1500s → 记分牌 19:15 (match_time=1155s)
- 由此推算 kickoff_video_offset = 345s
- 599 进球事件 time=1338000ms (22:18)，对应视频 1683s，帧上显示 22:18 ✓
"""
from __future__ import annotations
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
RECORDINGS_DIR = SCRIPT_DIR.parent
API_599_DIR = RECORDINGS_DIR.parent / "599_project"
for p in (str(RECORDINGS_DIR), str(API_599_DIR), str(SCRIPT_DIR)):
    if p not in sys.path:
        sys.path.insert(0, p)

from live_text_599 import AlignmentEngine, LiveTextPoller599, _event_time_ms, _event_text, _extract_score_from_text
from api_599 import get_match_info, get_all_live_text


def test_alignment_engine_basic():
    """测试 AlignmentEngine 的核心公式。"""
    print("=" * 60)
    print("TEST 1: AlignmentEngine 核心公式")
    print("=" * 60)

    # 模拟已知参数
    # 录制开始: seg_00000 created at 11:41:18 local (UTC+8) = 03:41:18 UTC
    video_start = datetime(2026, 3, 31, 3, 41, 18, tzinfo=timezone.utc)
    # 从视频帧确认: kickoff at video 345s
    kickoff_utc = video_start + timedelta(seconds=345)

    engine = AlignmentEngine(video_start_utc=video_start)
    # 直接设置 kickoff
    engine.kickoff_utc = kickoff_utc
    engine.kickoff_source = "manual_test"

    print(f"  video_start_utc: {video_start.isoformat()}")
    print(f"  kickoff_utc:     {kickoff_utc.isoformat()}")
    print(f"  kickoff_offset:  {engine.kickoff_video_offset():.1f}s (should be 345.0)")

    # 验证已知对齐点
    cases = [
        (255_000, 600, "4:15 frame"),
        (1155_000, 1500, "19:15 frame"),
        (1338_000, 1683, "22:18 goal"),
    ]
    all_pass = True
    for match_ms, expected_video, label in cases:
        video_pos = engine.match_time_to_video(match_ms)
        error = abs(video_pos - expected_video)
        ok = error < 1.0
        status = "PASS" if ok else "FAIL"
        print(f"  [{status}] {label}: match={match_ms/1000:.0f}s → video={video_pos:.1f}s (expected={expected_video}s, err={error:.1f}s)")
        if not ok:
            all_pass = False

    assert all_pass, "AlignmentEngine 公式验证失败"
    print("  >>> 全部通过\n")


def test_live_clock_inference():
    """测试用实时事件反推 kickoff_utc 的精度。"""
    print("=" * 60)
    print("TEST 2: 实时事件反推 kickoff_utc")
    print("=" * 60)

    engine = AlignmentEngine()
    # 模拟: 我们在比赛第 22:18 时观察到一批事件
    fake_events = [
        {"time": "1338000", "code": 2053, "msgId": "100", "msgText": "球进了!Goal~~~~ 0:1"},
        {"time": "1320000", "code": 1024, "msgId": "99", "msgText": "主队中场组织进攻"},
        {"time": "1300000", "code": 1051, "msgId": "98", "msgText": "主队后场控球"},
    ]
    # 假设我们是在比赛22:18后5秒（网络延迟）观察到的
    observed_at = datetime(2026, 4, 1, 2, 50, 23, tzinfo=timezone.utc)  # 某个绝对时间
    engine.update_from_live_events(fake_events, observed_at)

    # 反推的 kickoff 应该约等于 observed_at - 1338s = 02:28:05 UTC
    expected_kickoff = observed_at - timedelta(milliseconds=1338_000)
    actual_kickoff = engine.kickoff_utc
    drift = abs((actual_kickoff - expected_kickoff).total_seconds())

    print(f"  observed_at:     {observed_at.isoformat()}")
    print(f"  latest event:    22:18 (1338000ms)")
    print(f"  inferred kickoff: {actual_kickoff.isoformat()}")
    print(f"  expected kickoff: {expected_kickoff.isoformat()}")
    print(f"  drift: {drift:.3f}s")
    print(f"  source: {engine.kickoff_source}")

    assert drift < 0.1, f"反推 kickoff 偏差过大: {drift}s"
    print("  >>> 通过\n")


def test_score_drift_validation():
    """测试比分变化交叉验证。"""
    print("=" * 60)
    print("TEST 3: 比分变化交叉验证")
    print("=" * 60)

    engine = AlignmentEngine()
    engine.kickoff_utc = datetime(2026, 4, 1, 2, 28, 0, tzinfo=timezone.utc)

    # 模拟 betting_data 报告比分变化
    engine.observe_betting_score([
        {"score_h": "0", "score_c": "1", "timestamp": "2026-04-01T02:50:25+00:00"},
    ])

    # 599 的进球事件在 22:18 (1338000ms)
    # 按 kickoff_utc 换算: 02:28:00 + 22:18 = 02:50:18 UTC
    drift = engine.validate_score_drift("0-1", 1338_000)
    print(f"  599 goal at match 22:18 → wallclock 02:50:18")
    print(f"  betting reports 0-1 at 02:50:25")
    print(f"  drift: {drift}")
    assert drift is not None
    assert abs(drift["drift_sec"]) < 30, f"漂移过大: {drift['drift_sec']}s"
    print(f"  >>> 通过 (drift={drift['drift_sec']}s)\n")


def test_annotate_event():
    """测试事件标注输出格式。"""
    print("=" * 60)
    print("TEST 4: 事件标注")
    print("=" * 60)

    engine = AlignmentEngine(video_start_utc="2026-03-31T03:41:18+00:00")
    engine.kickoff_utc = datetime(2026, 3, 31, 3, 47, 3, tzinfo=timezone.utc)  # 345s后
    engine.kickoff_source = "test"

    event = {"time": "1338000", "code": 2053, "msgId": "100", "msgText": "球进了!Goal~~~~ 0:1"}
    annotated = engine.annotate_event(event)

    print(f"  video_pos: {annotated.get('_video_pos_sec')}s (expected ~1683.0)")
    print(f"  match_time_ms: {annotated.get('_match_time_ms')} (expected 1338000)")
    assert annotated["_video_pos_sec"] is not None
    assert abs(annotated["_video_pos_sec"] - 1683.0) < 1.0
    print("  >>> 通过\n")


def test_real_599_api():
    """用真实 599 API 数据测试 England vs Japan。"""
    print("=" * 60)
    print("TEST 5: 真实 599 API (England vs Japan)")
    print("=" * 60)

    tid = "4750710"
    print(f"  获取 599 数据 thirdId={tid}...")
    all_live = get_all_live_text(tid)
    print(f"  获取到 {len(all_live)} 条文字直播")

    # 找关键事件
    kickoff = None
    goals = []
    halftime = None
    fulltime = None
    for e in all_live:
        code = int(e.get("code", 0) or 0)
        t = _event_time_ms(e)
        text = _event_text(e)
        if code == 10 and t <= 120_000:
            kickoff = e
        if "Goal" in text or "进球" in text:
            score = _extract_score_from_text(text)
            if score:
                goals.append((t, score, text[:50]))
        if code == 1:
            halftime = e
        if code == 20 and fulltime is None:
            fulltime = e

    print(f"\n  开球: {_event_time_ms(kickoff) if kickoff else 'N/A'}ms")
    print(f"  进球: {goals}")
    print(f"  半场: {_event_time_ms(halftime) if halftime else 'N/A'}ms")
    print(f"  终场: {_event_time_ms(fulltime) if fulltime else 'N/A'}ms")

    # 用 AlignmentEngine 生成完整对齐
    engine = AlignmentEngine(video_start_utc="2026-03-31T03:41:18+00:00")
    engine.kickoff_utc = datetime(2026, 3, 31, 3, 47, 3, tzinfo=timezone.utc)
    engine.kickoff_source = "poc_ground_truth"

    print(f"\n  === 关键事件对齐结果 ===")
    key_events = [e for e in all_live if int(e.get("fontStyle", 0) or 0) >= 3 or int(e.get("code", 0) or 0) in {10, 13, 1, 20, 2053, 1029}]
    key_events.sort(key=lambda x: _event_time_ms(x))

    seen_texts = set()
    for e in key_events:
        text = _event_text(e)
        t_ms = _event_time_ms(e)
        if text in seen_texts or t_ms < 0:
            continue
        seen_texts.add(text)
        annotated = engine.annotate_event(e)
        vp = annotated.get("_video_pos_sec")
        if vp is not None:
            m, s = divmod(int(vp), 60)
            h, m = divmod(m, 60)
            vp_str = f"{h}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"
        else:
            vp_str = "N/A"
        match_m = t_ms // 60000
        match_s = (t_ms % 60000) // 1000
        print(f"  [{match_m:3d}:{match_s:02d}] video={vp_str:>8s} | code={str(e.get('code','')):>5s} | {text[:60]}")

    # 验证进球对齐
    if goals:
        goal_ms = goals[0][0]
        video_pos = engine.match_time_to_video(goal_ms)
        expected = 1683.0  # from our visual verification
        error = abs(video_pos - expected)
        print(f"\n  进球对齐验证: video={video_pos:.1f}s expected={expected:.1f}s error={error:.1f}s")
        assert error < 2.0, f"进球对齐误差过大: {error}s"
        print("  >>> 通过")

    print()


def test_match_resolution():
    """测试 599 比赛匹配（需要联网）。"""
    print("=" * 60)
    print("TEST 6: 比赛匹配 (England vs Japan)")
    print("=" * 60)

    poller = LiveTextPoller599(
        "England",
        "Japan",
        selected_match={"team_h": "England", "team_c": "Japan", "gtype": "FT", "league": "国际友谊"},
        poll_interval=999,  # 不真正轮询
    )
    found = poller._resolve_match()
    print(f"  匹配结果: {'成功' if found else '失败（昨天比赛已不在今日列表）'}")
    if found:
        print(f"  thirdId: {poller.third_id}")
        print(f"  resolved: {poller.resolved_home} vs {poller.resolved_away}")
        print(f"  matched_by: {poller.matched_by}")
        # 昨天的 England vs Japan 可能已不在今日列表
        if poller.third_id == "4750710":
            print("  >>> 精确匹配通过")
        else:
            print(f"  >>> 注意: 匹配到不同的比赛（今日列表已更新），这是预期行为")
    else:
        print(f"  error: {poller.last_error}")
        print("  >>> 未匹配（昨天比赛不在今日列表），预期行为")
    print()


if __name__ == "__main__":
    test_alignment_engine_basic()
    test_live_clock_inference()
    test_score_drift_validation()
    test_annotate_event()
    test_real_599_api()
    test_match_resolution()
    print("=" * 60)
    print("全部 POC 测试完成!")
    print("=" * 60)
