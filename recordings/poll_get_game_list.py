#!/usr/bin/env python3
"""Poll and parse the get_game_list XML feed into structured JSON snapshots."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import socket
import ssl
import sys
import time
import html
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from http.client import IncompleteRead
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


DEFAULT_URL = "https://112.121.42.168/transform.php?ver=2026-03-19-fireicon_142"
DEFAULT_GTYPE_ORDER = ("FT", "BK", "ES", "TN", "VB", "BM", "TT", "BS", "SK", "OP")
SHOWTYPE_TO_RTYPE = {
    "live": "rb",
    "today": "r",
    "early": "r",
}
GTYPE_LABELS = {
    "FT": "足球",
    "BK": "篮球",
    "ES": "电子竞技",
    "TN": "网球",
    "VB": "排球",
    "BM": "羽毛球",
    "TT": "乒乓球",
    "BS": "棒球",
    "SK": "斯诺克",
    "OP": "其他",
}
CATEGORY_LABELS = {
    "base": "基础信息",
    "scoreboard": "比分拆分",
    "main": "主盘口",
    "handicap": "让球",
    "totals": "大小/总分",
    "team_totals": "球队大小",
    "moneyline": "独赢",
    "odd_even": "单双",
    "halves": "半场",
    "periods": "节/盘/局",
    "points": "分点玩法",
    "goals": "进球相关",
    "corners": "角球",
    "bookings": "牌数",
    "intervals": "区间玩法",
    "rmix_ou": "让球/大小混合",
    "specials": "特殊玩法",
}
FT_LIVE_FILTER_CODES = {
    "rmix_ou": {
        "RE", "HRE", "ROU", "HROU", "AROU", "BROU", "DROU", "EROU",
        "ROUH", "ROUC", "HRUH", "HRUC", "TARU", "TBRU", "TDRU", "TERU",
    },
    "goals": {
        "ROU", "HROU", "RPD", "HRPD", "AROU", "BROU", "DROU", "EROU",
        "RT", "HRT", "RTS", "RTS2", "ROUH", "ROUC", "HRUH", "HRUC",
        "ARG", "BRG", "CRG", "DRG", "ERG", "FRG", "GRG", "HRG", "IRG",
        "JRG", "KRG", "LRG", "MRG", "NRG", "ORG", "RWM", "RTW", "RCS",
        "RWN", "RMOU", "RMTS", "ROUT", "RMPG", "RHG", "RMG", "RSB",
        "RT3G", "RT1G", "RDU", "RDS", "RDG", "ROUE", "ROUP", "RPF",
    },
    "halves": {
        "HRE", "HROU", "HRM", "HREO", "HRPD", "HRT", "RTS2", "HRUH",
        "HRUC", "RF", "RHG", "RMG", "RSB", "RWE", "RWB",
    },
    "intervals": {
        "AROU", "BROU", "DROU", "EROU", "TARU", "TBRU", "TDRU", "TERU",
    },
    "corners": {
        "RNC1", "RNC2", "RNC3", "RNC4", "RNC5", "RNC6", "RNC7", "RNC8",
        "RNC9", "RNCA", "RNCB", "RNCC", "RNCD", "RNCE", "RNCF", "RNCG",
        "RNCH", "RNCI", "RNCJ", "RNCK", "RNCL", "RNCM", "RNCN", "RNCO",
        "RNCP", "RNCQ", "RNCR", "RNCS", "RNCT", "RNCU",
    },
    "bookings": {
        "RNBA", "RNBB", "RNBC", "RNBD", "RNBE", "RNBF", "RNBG", "RNBH",
        "RNBI", "RNBJ", "RNBK", "RNBL", "RNBM", "RNBN", "RNBO",
    },
}
CATEGORY_ORDER = [
    "base",
    "scoreboard",
    "main",
    "handicap",
    "totals",
    "team_totals",
    "moneyline",
    "odd_even",
    "halves",
    "periods",
    "points",
    "goals",
    "corners",
    "bookings",
    "intervals",
    "rmix_ou",
    "specials",
]


def is_filled(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, str):
        return value.strip() != ""
    if isinstance(value, dict):
        return any(is_filled(item) for item in value.values())
    if isinstance(value, (list, tuple, set)):
        return any(is_filled(item) for item in value)
    return True


def drop_empty_dict(values: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in values.items() if is_filled(value)}


def ensure_category_slot(categories: dict[str, Any], category: str) -> dict[str, Any]:
    slot = categories.get(category)
    if slot is None:
        slot = {
            "label": CATEGORY_LABELS.get(category, category),
            "items": [],
        }
        categories[category] = slot
    return slot


def add_category_item(categories: dict[str, Any], category: str, item: dict[str, Any]) -> None:
    ensure_category_slot(categories, category)["items"].append(item)


def finalize_categories(categories: dict[str, Any]) -> tuple[dict[str, Any], dict[str, int]]:
    ordered: dict[str, Any] = {}
    counts: dict[str, int] = {}
    for category in CATEGORY_ORDER:
        slot = categories.get(category)
        if slot and slot["items"]:
            ordered[category] = slot
            counts[category] = len(slot["items"])
    for category, slot in categories.items():
        if category not in ordered and slot["items"]:
            ordered[category] = slot
            counts[category] = len(slot["items"])
    return ordered, counts


def build_standard_field_categories(fields: dict[str, Any]) -> tuple[dict[str, Any], dict[str, int]]:
    categories: dict[str, Any] = {}
    market_specs = [
        (
            "handicap",
            "让球",
            {
                "ratio": fields.get("RATIO_RE", ""),
                "home": fields.get("IOR_REH", ""),
                "away": fields.get("IOR_REC", ""),
                "strong": fields.get("STRONG", ""),
            },
        ),
        (
            "totals",
            "大小/总分",
            {
                "ratio_over": fields.get("RATIO_ROUO", "") or fields.get("RATIO_ROUU", ""),
                "ratio_under": fields.get("RATIO_ROUU", "") or fields.get("RATIO_ROUO", ""),
                "over": fields.get("IOR_ROUH", ""),
                "under": fields.get("IOR_ROUC", ""),
            },
        ),
        (
            "team_totals",
            "主队大小",
            {
                "ratio_over": fields.get("RATIO_ROUHO", "") or fields.get("RATIO_ROUHU", ""),
                "ratio_under": fields.get("RATIO_ROUHU", "") or fields.get("RATIO_ROUHO", ""),
                "over": fields.get("IOR_ROUHO", ""),
                "under": fields.get("IOR_ROUHU", ""),
            },
        ),
        (
            "team_totals",
            "客队大小",
            {
                "ratio_over": fields.get("RATIO_ROUCO", "") or fields.get("RATIO_ROUCU", ""),
                "ratio_under": fields.get("RATIO_ROUCU", "") or fields.get("RATIO_ROUCO", ""),
                "over": fields.get("IOR_ROUCO", ""),
                "under": fields.get("IOR_ROUCU", ""),
            },
        ),
        (
            "moneyline",
            "独赢",
            {
                "home": fields.get("IOR_RMH", ""),
                "draw": fields.get("IOR_RMN", ""),
                "away": fields.get("IOR_RMC", ""),
            },
        ),
        (
            "halves",
            "半场独赢",
            {
                "home": fields.get("IOR_HRMH", ""),
                "draw": fields.get("IOR_HRMN", ""),
                "away": fields.get("IOR_HRMC", ""),
            },
        ),
        (
            "odd_even",
            "单双",
            {
                "odd": fields.get("IOR_REOO", ""),
                "even": fields.get("IOR_REOE", ""),
            },
        ),
        (
            "goals",
            "双方进球",
            {
                "yes": fields.get("IOR_RTSY", ""),
                "no": fields.get("IOR_RTSN", ""),
            },
        ),
    ]
    for category, label, values in market_specs:
        clean = drop_empty_dict(values)
        if clean:
            add_category_item(
                categories,
                category,
                {
                    "name": label,
                    "source": "inline_fields",
                    "values": clean,
                },
            )
    return finalize_categories(categories)


def classify_json_market(play_key: str, market_code: str, market_values: dict[str, Any]) -> str:
    code = market_code.upper()
    values_upper = " ".join(str(key).upper() for key in market_values.keys())
    period_like = code.startswith("MS_") or is_filled(market_values.get("MS")) or play_key.upper() not in {"PLAY1", "PLAY2", "PLAY3", "PLAY4", "PLAY5"}

    if "OUH" in code or "OUC" in code:
        return "team_totals" if not period_like else "periods"
    if "EO" in code:
        return "odd_even" if not period_like else "periods"
    if "POINT" in code or "POINT" in values_upper:
        return "points"
    if code in {"RM", "HRM", "M", "MS_M"} or code.endswith("_M"):
        return "moneyline" if not period_like else "periods"
    if "OU" in code:
        return "totals" if not period_like else "periods"
    if code in {"R", "RE", "HRE", "RG", "RGA"}:
        return "handicap" if not period_like else "periods"
    if period_like:
        return "periods"
    return "specials"


def build_json_categories(
    gtype: str,
    fields: dict[str, Any],
    nested: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, int]]:
    categories: dict[str, Any] = {}

    score = nested.get("SCORE")
    if isinstance(score, dict):
        add_category_item(
            categories,
            "scoreboard",
            {
                "name": "比分拆分",
                "source": "SCORE",
                "values": drop_empty_dict(score),
            },
        )

    for play_key, play_block in nested.items():
        if play_key == "SCORE" or not isinstance(play_block, dict):
            continue
        for market_code, market_values in play_block.items():
            if not isinstance(market_values, dict):
                continue
            clean_values = drop_empty_dict(market_values)
            if not clean_values:
                continue
            category = classify_json_market(play_key, market_code, market_values)
            add_category_item(
                categories,
                category,
                {
                    "name": market_code,
                    "source": play_key,
                    "values": clean_values,
                },
            )

    standard_categories, _ = build_standard_field_categories(fields)
    for category, slot in standard_categories.items():
        ensure_category_slot(categories, category)["items"].extend(slot["items"])

    if gtype.upper() in {"FT", "TN", "BS"} and is_filled(fields.get("SCOREGAMEH")):
        add_category_item(
            categories,
            "points",
            {
                "name": "当前分点",
                "source": "inline_fields",
                "values": drop_empty_dict(
                    {
                        "serve": fields.get("SERVE", ""),
                        "game_h": fields.get("SCOREGAMEH", ""),
                        "game_c": fields.get("SCOREGAMEC", ""),
                        "point_h": fields.get("SCOREPOINTH", ""),
                        "point_c": fields.get("SCOREPOINTC", ""),
                    }
                ),
            },
        )

    return finalize_categories(categories)


def categorize_ft_more_anchor(anchor: str) -> str:
    upper_anchor = anchor.upper()
    if upper_anchor in FT_LIVE_FILTER_CODES["corners"]:
        return "corners"
    if upper_anchor in FT_LIVE_FILTER_CODES["bookings"]:
        return "bookings"
    if upper_anchor in FT_LIVE_FILTER_CODES["intervals"]:
        return "intervals"
    if upper_anchor in FT_LIVE_FILTER_CODES["halves"]:
        return "halves"
    if upper_anchor in FT_LIVE_FILTER_CODES["goals"]:
        return "goals"
    if upper_anchor in FT_LIVE_FILTER_CODES["rmix_ou"]:
        return "rmix_ou"
    if upper_anchor.startswith("RNC"):
        return "corners"
    if upper_anchor.startswith("RNB"):
        return "bookings"
    return "main"


def field_matches_anchor(field_key: str, anchor: str) -> bool:
    key_upper = field_key.upper()
    if key_upper == f"SW_{anchor}" or key_upper == anchor:
        return True
    tokens = re.split(r"[_:]", key_upper)
    return any(token.startswith(anchor) for token in tokens[1:])


def build_ft_more_categories(fields: dict[str, Any]) -> tuple[dict[str, Any], dict[str, int], int]:
    categories: dict[str, Any] = {}
    anchors = sorted(
        {
            key[3:].upper()
            for key, value in fields.items()
            if key.startswith("sw_") and is_filled(value)
        }
    )
    matched_keys: set[str] = set()

    for anchor in anchors:
        group_fields = {
            key: value
            for key, value in fields.items()
            if field_matches_anchor(key, anchor) and is_filled(value)
        }
        if not group_fields:
            continue
        matched_keys.update(group_fields.keys())
        add_category_item(
            categories,
            categorize_ft_more_anchor(anchor),
            {
                "name": anchor,
                "source": "get_game_more",
                "values": group_fields,
            },
        )

    base_fields = {
        key: value
        for key, value in fields.items()
        if key not in matched_keys and is_filled(value)
    }
    if base_fields:
        add_category_item(
            categories,
            "base",
            {
                "name": "基础字段",
                "source": "get_game_more",
                "values": base_fields,
            },
        )

    ordered, counts = finalize_categories(categories)
    market_count = sum(counts.values())
    return ordered, counts, market_count


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def read_text(
    *,
    inline_value: str | None = None,
    file_path: str | None = None,
    env_name: str | None = None,
) -> str | None:
    if inline_value:
        return inline_value
    if file_path:
        return Path(file_path).read_text(encoding="utf-8").strip()
    if env_name:
        value = os.environ.get(env_name, "").strip()
        return value or None
    return None


def elem_text(parent: ET.Element, tag: str) -> str:
    child = parent.find(tag)
    if child is None or child.text is None:
        return ""
    return child.text


def parse_game(game: ET.Element, ec: ET.Element) -> dict[str, Any]:
    fields: dict[str, str] = {}
    for child in game:
        fields[child.tag] = child.text or ""
    categories, category_counts = build_standard_field_categories(fields)

    result: dict[str, Any] = {
        "ec_id": ec.get("id", ""),
        "has_ec": ec.get("hasEC", ""),
        "game_node_id": game.get("id", ""),
        "fields": fields,
        "categories": categories,
        "category_counts": category_counts,
    }

    # Promote the most commonly-used keys for easier downstream querying.
    for key in (
        "ECID",
        "GID",
        "HGID",
        "LID",
        "LEAGUE",
        "TEAM_H",
        "TEAM_C",
        "DATETIME",
        "RETIMESET",
        "NOW_MODEL",
        "SCORE_H",
        "SCORE_C",
        "IS_RB",
        "RUNNING",
        "GOPEN",
        "HGOPEN",
        "MORE",
    ):
        result[key.lower()] = fields.get(key, "")

    return result


def parse_game_list_xml(xml_text: str) -> dict[str, Any]:
    root = ET.fromstring(xml_text)
    games: list[dict[str, Any]] = []

    for ec in root.findall("ec"):
        for game in ec.findall("game"):
            games.append(parse_game(game, ec))

    payload = {
        "meta": {
            "parsed_at": utc_now_iso(),
            "sip": root.get("sip", ""),
            "dataCount": elem_text(root, "dataCount"),
            "totalDataCount": elem_text(root, "totalDataCount"),
            "pageCount": elem_text(root, "pageCount"),
        },
        "games": games,
    }
    return payload


def compact_json_game_fields(game: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    fields: dict[str, Any] = {}
    nested: dict[str, Any] = {}
    for key, value in game.items():
        if isinstance(value, dict):
            nested[key] = value
        else:
            fields[key] = value
    return fields, nested


def parse_json_game(game_key: str, game: dict[str, Any]) -> dict[str, Any]:
    fields, nested = compact_json_game_fields(game)
    score_h = fields.get("SCORE_H")
    score_c = fields.get("SCORE_C")

    if score_h in (None, "") and isinstance(game.get("SCORE"), dict):
        score_h = game["SCORE"].get("GAME_H", "")
    if score_c in (None, "") and isinstance(game.get("SCORE"), dict):
        score_c = game["SCORE"].get("GAME_C", "")

    retimeset = (
        fields.get("RETIMESET")
        or fields.get("STATUS")
        or fields.get("NOWSESSION")
        or ""
    )
    categories, category_counts = build_json_categories(
        fields.get("MT_GTYPE", "") or fields.get("GTYPE", ""),
        fields,
        nested,
    )

    result: dict[str, Any] = {
        "ec_id": fields.get("ECID", ""),
        "has_ec": "",
        "game_node_id": game_key,
        "fields": fields,
        "nested": nested,
        "raw": game,
        "categories": categories,
        "category_counts": category_counts,
        "ecid": fields.get("ECID", ""),
        "gid": fields.get("GID", ""),
        "hgid": fields.get("HGID", ""),
        "lid": fields.get("LID", ""),
        "league": fields.get("LEAGUE", ""),
        "team_h": fields.get("TEAM_H", ""),
        "team_c": fields.get("TEAM_C", ""),
        "datetime": fields.get("DATETIME", "") or fields.get("GAME_DATE_TIME", ""),
        "retimeset": retimeset,
        "now_model": fields.get("NOW_MODEL", ""),
        "score_h": score_h or "",
        "score_c": score_c or "",
        "is_rb": fields.get("IS_RB", ""),
        "running": fields.get("RUNNING", ""),
        "gopen": fields.get("GOPEN", ""),
        "hgopen": fields.get("HGOPEN", ""),
        "more": fields.get("MORE", ""),
    }
    return result


def parse_game_list_json(json_text: str) -> dict[str, Any]:
    data = json.loads(json_text)
    response = data.get("response", {})
    games: list[dict[str, Any]] = []

    if isinstance(response, dict):
        for game_key, game in response.items():
            if isinstance(game, dict):
                games.append(parse_json_game(game_key, game))
    elif isinstance(response, list):
        for idx, game in enumerate(response):
            if isinstance(game, dict):
                games.append(parse_json_game(f"GAME_{idx}", game))

    return {
        "meta": {
            "parsed_at": utc_now_iso(),
            "format": "json",
            "status": data.get("status", ""),
            "gameCount": len(games),
        },
        "games": games,
        "phpData": data.get("phpData", {}),
        "original_json": data.get("original_json", {}),
    }


def parse_game_list_response(raw_text: str) -> dict[str, Any]:
    stripped = raw_text.lstrip()
    if stripped.startswith("<?xml"):
        payload = parse_game_list_xml(raw_text)
        payload["meta"]["format"] = "xml"
        return payload
    if stripped.startswith("{") or stripped.startswith("["):
        return parse_game_list_json(raw_text)
    raise ValueError(f"unsupported response format: {stripped[:80]!r}")


def parse_game_more_xml(xml_text: str) -> dict[str, Any]:
    root = ET.fromstring(xml_text)
    game = root.find("game")
    fields: dict[str, str] = {}
    if game is not None:
        for child in game:
            fields[child.tag] = child.text or ""
    categories, category_counts, market_count = build_ft_more_categories(fields)

    return {
        "meta": {
            "parsed_at": utc_now_iso(),
            "sip": root.get("sip", ""),
            "code": elem_text(root, "code"),
            "systime": elem_text(root, "systime"),
        },
        "game": {
            "id": game.get("id", "") if game is not None else "",
            "master": game.get("master", "") if game is not None else "",
            "mode": game.get("mode", "") if game is not None else "",
            "ptype": game.get("ptype", "") if game is not None else "",
            "fields": fields,
            "categories": categories,
            "category_counts": category_counts,
            "market_count": market_count,
        },
    }


def fetch_xml(url: str, body: str, cookie: str | None, timeout: float) -> str:
    def _cookie_fingerprint(raw_cookie: str | None) -> str:
        text = str(raw_cookie or "").strip()
        if not text:
            return "none"
        return hashlib.md5(text.encode("utf-8")).hexdigest()[:10]

    def _request_context() -> str:
        parsed_url = urllib.parse.urlparse(url)
        values = parse_form_body(body)
        return (
            f"host={parsed_url.netloc or '?'} "
            f"path={parsed_url.path or '/'} "
            f"ver={urllib.parse.parse_qs(parsed_url.query).get('ver', [''])[0] or values.get('ver', '') or '?'} "
            f"p={values.get('p', '?')} "
            f"gtype={values.get('gtype', '?')} "
            f"showtype={values.get('showtype', '?')} "
            f"rtype={values.get('rtype', '?')} "
            f"cookie_md5={_cookie_fingerprint(cookie)} "
            f"cookie_len={len(cookie or '')}"
        )

    data = body.encode("utf-8")
    request = urllib.request.Request(url, data=data, method="POST")
    request.add_header("Content-Type", "application/x-www-form-urlencoded")
    request.add_header("Accept", "*/*")
    request.add_header("User-Agent", "Mozilla/5.0")
    if cookie:
        request.add_header("Cookie", cookie)

    # The target uses an invalid / self-signed certificate.
    context = ssl._create_unverified_context()
    last_error: Exception | None = None
    for _attempt in range(2):
        try:
            with urllib.request.urlopen(request, timeout=timeout, context=context) as resp:
                return resp.read().decode("utf-8", errors="replace")
        except IncompleteRead as exc:
            return exc.partial.decode("utf-8", errors="replace")
        except urllib.error.HTTPError as exc:
            body_preview = ""
            try:
                preview = exc.read(180)
                body_preview = preview.decode("utf-8", errors="replace").strip().replace("\n", " ")[:180]
            except Exception:
                body_preview = ""
            last_error = RuntimeError(
                f"HTTP {exc.code}: {exc.reason} | {_request_context()}"
                + (f" | body={body_preview!r}" if body_preview else "")
            )
            if exc.code == 404:
                # 404 likely means the sing-box node is geo-blocked —
                # trigger node re-probe on last retry
                if _attempt >= 1:
                    try:
                        from data_site_node_prober import handle_data_site_failure
                        handle_data_site_failure()
                    except Exception:
                        pass
                time.sleep(0.35)
                continue
        except (socket.timeout, TimeoutError, urllib.error.URLError, OSError) as exc:
            last_error = exc
            time.sleep(0.35)
    if last_error is not None:
        raise last_error
    raise RuntimeError("request failed without an exception")


def next_ts_ms() -> str:
    return str(int(time.time() * 1000))


def parse_form_body(body: str) -> dict[str, str]:
    return {
        key: value
        for key, value in urllib.parse.parse_qsl(
            body,
            keep_blank_values=True,
        )
    }


def encode_form_body(values: dict[str, str]) -> str:
    return urllib.parse.urlencode(values)


def normalize_gtypes(raw: str | None) -> list[str]:
    if not raw:
        return [DEFAULT_GTYPE_ORDER[0]]
    tokens = [token.strip().upper() for token in raw.split(",")]
    return [token for token in tokens if token]


def build_game_list_body(
    template: dict[str, str],
    *,
    gtype: str,
    showtype: str,
    rtype: str,
) -> str:
    values = dict(template)
    values["p"] = "get_game_list"
    values["gtype"] = gtype.lower()
    values["showtype"] = showtype
    values["rtype"] = rtype
    values["ts"] = values.get("ts") or next_ts_ms()
    values["chgSortTS"] = next_ts_ms()
    values.setdefault("langx", "zh-cn")
    values.setdefault("p3type", "")
    values.setdefault("date", "")
    values.setdefault("ltype", "3")
    values.setdefault("filter", "")
    values.setdefault("cupFantasy", "N")
    values.setdefault("sorttype", "L")
    values.setdefault("specialClick", "")
    values.setdefault("isFantasy", "N")
    return encode_form_body(values)


def build_game_more_body(
    template: dict[str, str],
    *,
    game_fields: dict[str, str],
    gtype: str,
    showtype: str,
    is_rb: str,
    more_filter: str,
) -> str:
    values = dict(template)
    values["p"] = "get_game_more"
    values["gtype"] = gtype.lower()
    values["showtype"] = showtype
    values["ltype"] = values.get("ltype") or "3"
    values["isRB"] = is_rb
    values["lid"] = game_fields.get("LID", "")
    values["specialClick"] = ""
    values["mode"] = "NORMAL"
    values["from"] = "game_more"
    values["filter"] = more_filter
    values["ts"] = next_ts_ms()
    values["ecid"] = game_fields.get("ECID", "")
    keep_keys = (
        "uid",
        "ver",
        "langx",
        "p",
        "gtype",
        "showtype",
        "ltype",
        "isRB",
        "lid",
        "specialClick",
        "mode",
        "from",
        "filter",
        "ts",
        "ecid",
    )
    trimmed = {key: values.get(key, "") for key in keep_keys}
    return encode_form_body(trimmed)


def ensure_output_dir(path: str) -> Path:
    outdir = Path(path)
    outdir.mkdir(parents=True, exist_ok=True)
    return outdir


def write_snapshot(outdir: Path, parsed: dict[str, Any], raw_xml: str) -> Path:
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    snapshot_path = outdir / f"snapshot-{timestamp}.json"
    payload = {
        "snapshot_time": utc_now_iso(),
        "parsed": parsed,
        "raw_xml": raw_xml,
    }
    snapshot_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    latest_path = outdir / "latest.json"
    latest_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return snapshot_path


def write_multi_snapshot(outdir: Path, payload: dict[str, Any]) -> Path:
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    snapshot_path = outdir / f"snapshot-{timestamp}.json"
    snapshot_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    latest_path = outdir / "latest.json"
    latest_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return snapshot_path


def write_latest(outdir: Path, payload: dict[str, Any]) -> None:
    """Write only latest.json (overwrite). No timestamped archive."""
    latest_path = outdir / "latest.json"
    latest_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def collect_live_payload(
    args: argparse.Namespace,
    *,
    cookie: str | None,
    template: dict[str, str],
    gtypes: list[str],
    showtype: str,
    rtype: str,
) -> tuple[dict[str, Any], int, int, dict[str, str]]:
    started_at = utc_now_iso()
    payload: dict[str, Any] = {
        "snapshot_time": started_at,
        "inputs": {
            "gtypes": gtypes,
            "showtype": showtype,
            "rtype": rtype,
            "include_more": args.include_more,
            "more_filter": args.more_filter,
        },
        "feeds": {},
    }

    total_games = 0
    total_more = 0
    errors: dict[str, str] = {}

    for gtype in gtypes:
        list_body = build_game_list_body(
            template,
            gtype=gtype,
            showtype=showtype,
            rtype=rtype,
        )
        try:
            raw_xml = fetch_xml(args.url, list_body, cookie, args.timeout)
            parsed = parse_game_list_response(raw_xml)
            feed_entry: dict[str, Any] = {
                "request_body": list_body,
                "parsed": parsed,
                "raw_response": raw_xml,
            }

            if args.include_more and gtype == "FT":
                more_payloads: dict[str, Any] = {}
                more_errors: dict[str, str] = {}
                is_rb = "Y" if showtype == "live" else "N"
                for game in parsed["games"]:
                    fields = game["fields"]
                    if not fields.get("ECID"):
                        continue
                    ecid = str(fields["ECID"])
                    try:
                        more_body = build_game_more_body(
                            template,
                            game_fields=fields,
                            gtype=gtype,
                            showtype=showtype,
                            is_rb=is_rb,
                            more_filter=args.more_filter,
                        )
                        more_xml = fetch_xml(args.url, more_body, cookie, args.timeout)
                        more_payloads[ecid] = {
                            "request_body": more_body,
                            "parsed": parse_game_more_xml(more_xml),
                            "raw_response": more_xml,
                        }
                        total_more += 1
                    except Exception as exc:
                        more_errors[ecid] = str(exc)
                    if args.more_delay > 0:
                        time.sleep(args.more_delay)
                if more_payloads:
                    feed_entry["game_more"] = more_payloads
                if more_errors:
                    feed_entry["game_more_errors"] = more_errors

                for game in parsed["games"]:
                    ecid = game.get("ecid") or game.get("fields", {}).get("ECID", "")
                    if not ecid or ecid not in more_payloads:
                        continue
                    more_game = more_payloads[ecid]["parsed"]["game"]
                    game["detail"] = {
                        "fields": more_game.get("fields", {}),
                        "categories": more_game.get("categories", {}),
                        "category_counts": more_game.get("category_counts", {}),
                        "market_count": more_game.get("market_count", 0),
                    }

            payload["feeds"][gtype] = feed_entry
            total_games += len(parsed["games"])
        except Exception as exc:
            errors[gtype] = str(exc)
            payload["feeds"][gtype] = {
                "request_body": list_body,
                "error": str(exc),
            }

    return payload, total_games, total_more, errors


def summarize_payload(
    payload: dict[str, Any],
    *,
    snapshot: Path,
    total_games: int,
    total_more: int,
    errors: dict[str, str],
    dashboard_path: Path | None = None,
) -> dict[str, Any]:
    summary = {
        "saved": str(snapshot),
        "sports": {
            gtype: len(feed.get("parsed", {}).get("games", []))
            for gtype, feed in payload["feeds"].items()
        },
        "total_games": total_games,
        "game_more": total_more,
    }
    if errors:
        summary["errors"] = errors
    if dashboard_path:
        summary["dashboard"] = str(dashboard_path)
    return summary


def sport_label(gtype: str) -> str:
    return GTYPE_LABELS.get(gtype.upper(), gtype.upper())


def build_dashboard_html(payload: dict[str, Any], title: str) -> str:
    snapshot_time = payload.get("snapshot_time", "")
    inputs = payload.get("inputs", {})
    feeds = payload.get("feeds", {})
    cards: list[str] = []
    sections: list[str] = []

    for gtype, feed in feeds.items():
        parsed = feed.get("parsed", {})
        games = parsed.get("games", [])
        counts_by_league: dict[str, int] = {}
        for game in games:
            league = game.get("league") or game.get("fields", {}).get("LEAGUE", "")
            counts_by_league[league] = counts_by_league.get(league, 0) + 1

        cards.append(
            f"""
            <div class="summary-card">
              <div class="summary-label">{html.escape(sport_label(gtype))}</div>
              <div class="summary-value">{len(games)}</div>
              <div class="summary-meta">{len(counts_by_league)} 个联赛</div>
            </div>
            """
        )

        league_rows = "".join(
            f"<div class='league-row'><span>{html.escape(name)}</span><strong>{count}</strong></div>"
            for name, count in sorted(counts_by_league.items(), key=lambda item: (-item[1], item[0]))[:12]
        )

        match_rows: list[str] = []
        more_map = feed.get("game_more", {})
        for game in games[:30]:
            fields = game.get("fields", {})
            ecid = fields.get("ECID", "")
            more_fields = (
                more_map.get(ecid, {})
                .get("parsed", {})
                .get("game", {})
                .get("fields", {})
            )
            more_count = game.get("more", "")
            score = f"{game.get('score_h', '')} : {game.get('score_c', '')}"
            odds_line = " / ".join(
                part for part in (
                    f"让球 {fields.get('RATIO_RE', '')} {fields.get('IOR_REH', '')}/{fields.get('IOR_REC', '')}".strip(),
                    f"大小 {fields.get('RATIO_ROUO', '')} {fields.get('IOR_ROUH', '')}/{fields.get('IOR_ROUC', '')}".strip(),
                    f"独赢 {fields.get('IOR_RMH', '')}/{fields.get('IOR_RMN', '')}/{fields.get('IOR_RMC', '')}".strip(),
                ) if part
            )
            markets = ""
            if more_fields:
                live_open = sum(1 for key, value in more_fields.items() if key.startswith("sw_") and value == "Y")
                markets = f"<div class='match-markets'>全盘口开关: {live_open} 项</div>"
            match_rows.append(
                f"""
                <article class="match-card">
                  <div class="match-top">
                    <div>
                      <div class="match-league">{html.escape(game.get('league', ''))}</div>
                      <div class="match-title">{html.escape(game.get('team_h', ''))} vs {html.escape(game.get('team_c', ''))}</div>
                    </div>
                    <div class="match-score">{html.escape(score)}</div>
                  </div>
                  <div class="match-meta">
                    <span>{html.escape(game.get('retimeset', ''))}</span>
                    <span>ECID {html.escape(ecid)}</span>
                    <span>更多 {html.escape(str(more_count))}</span>
                    <span>{html.escape(fields.get('GOPEN', ''))}/{html.escape(fields.get('HGOPEN', ''))}</span>
                  </div>
                  <div class="match-odds">{html.escape(odds_line)}</div>
                  {markets}
                </article>
                """
            )

        sections.append(
            f"""
            <section class="sport-section">
              <div class="section-head">
                <h2>{html.escape(sport_label(gtype))}</h2>
                <div class="section-meta">{len(games)} 场实时比赛</div>
              </div>
              <div class="section-grid">
                <div class="league-panel">
                  <h3>联赛分布</h3>
                  {league_rows or "<div class='empty'>暂无数据</div>"}
                </div>
                <div class="matches-panel">
                  <h3>比赛快照</h3>
                  {''.join(match_rows) or "<div class='empty'>暂无数据</div>"}
                </div>
              </div>
            </section>
            """
        )

    raw_json = html.escape(json.dumps(payload, ensure_ascii=False))
    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <meta http-equiv="refresh" content="5">
  <title>{html.escape(title)}</title>
  <style>
    :root {{
      --bg: #0b1220;
      --panel: #111b2e;
      --panel-2: #15233d;
      --line: #284062;
      --text: #e8eef8;
      --muted: #9eb0cb;
      --accent: #59d0ff;
      --accent-2: #ffd166;
      --danger: #ff6b6b;
      --ok: #7bd88f;
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      font-family: ui-sans-serif, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      background:
        radial-gradient(circle at top right, rgba(89,208,255,0.18), transparent 24%),
        radial-gradient(circle at top left, rgba(255,209,102,0.12), transparent 22%),
        linear-gradient(180deg, #07101d 0%, #0b1220 100%);
      color: var(--text);
    }}
    .wrap {{
      width: min(1600px, calc(100vw - 32px));
      margin: 0 auto;
      padding: 24px 0 64px;
    }}
    .hero {{
      display: flex;
      justify-content: space-between;
      gap: 24px;
      align-items: end;
      margin-bottom: 20px;
    }}
    .hero h1 {{
      margin: 0;
      font-size: 34px;
      line-height: 1.05;
      letter-spacing: -0.03em;
    }}
    .hero p {{
      margin: 10px 0 0;
      color: var(--muted);
    }}
    .stamp {{
      background: rgba(17,27,46,0.78);
      border: 1px solid var(--line);
      border-radius: 16px;
      padding: 14px 16px;
      color: var(--muted);
      min-width: 280px;
    }}
    .summary {{
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(160px, 1fr));
      gap: 12px;
      margin-bottom: 24px;
    }}
    .summary-card, .league-panel, .matches-panel, .raw-panel {{
      background: linear-gradient(180deg, rgba(21,35,61,0.95), rgba(12,20,35,0.95));
      border: 1px solid var(--line);
      border-radius: 18px;
      box-shadow: 0 18px 50px rgba(0,0,0,0.28);
    }}
    .summary-card {{
      padding: 16px;
    }}
    .summary-label {{
      color: var(--muted);
      font-size: 13px;
    }}
    .summary-value {{
      font-size: 36px;
      font-weight: 700;
      margin-top: 6px;
    }}
    .summary-meta {{
      color: var(--accent);
      margin-top: 8px;
      font-size: 13px;
    }}
    .sport-section {{
      margin-top: 28px;
    }}
    .section-head {{
      display: flex;
      justify-content: space-between;
      align-items: baseline;
      margin-bottom: 12px;
    }}
    .section-head h2, .league-panel h3, .matches-panel h3 {{
      margin: 0;
    }}
    .section-meta {{
      color: var(--accent-2);
      font-size: 14px;
    }}
    .section-grid {{
      display: grid;
      grid-template-columns: 320px 1fr;
      gap: 14px;
    }}
    .league-panel, .matches-panel {{
      padding: 16px;
    }}
    .league-row {{
      display: flex;
      justify-content: space-between;
      gap: 12px;
      padding: 10px 0;
      border-bottom: 1px solid rgba(40,64,98,0.65);
      color: var(--muted);
    }}
    .league-row strong {{
      color: var(--text);
    }}
    .match-card {{
      padding: 14px;
      border: 1px solid rgba(40,64,98,0.65);
      border-radius: 14px;
      background: rgba(9,16,28,0.55);
      margin-bottom: 10px;
    }}
    .match-top {{
      display: flex;
      justify-content: space-between;
      gap: 12px;
      align-items: start;
    }}
    .match-league {{
      color: var(--accent);
      font-size: 12px;
      margin-bottom: 4px;
    }}
    .match-title {{
      font-weight: 700;
      font-size: 17px;
    }}
    .match-score {{
      font-size: 24px;
      font-weight: 700;
      color: var(--accent-2);
      white-space: nowrap;
    }}
    .match-meta, .match-odds, .match-markets {{
      margin-top: 10px;
      color: var(--muted);
      font-size: 13px;
      display: flex;
      flex-wrap: wrap;
      gap: 10px 14px;
    }}
    .match-odds {{
      color: var(--text);
      font-size: 14px;
    }}
    .empty {{
      color: var(--muted);
      padding: 12px 0;
    }}
    .raw-panel {{
      margin-top: 28px;
      padding: 16px;
    }}
    .raw-panel pre {{
      margin: 0;
      white-space: pre-wrap;
      word-break: break-word;
      color: #bfd0ea;
      font-size: 12px;
      max-height: 320px;
      overflow: auto;
    }}
    @media (max-width: 980px) {{
      .hero, .section-head {{
        display: block;
      }}
      .stamp {{
        margin-top: 14px;
      }}
      .section-grid {{
        grid-template-columns: 1fr;
      }}
    }}
  </style>
</head>
<body>
  <div class="wrap">
    <section class="hero">
      <div>
        <h1>{html.escape(title)}</h1>
        <p>实时结构化比赛数据 + 人可读看板。当前模式: {html.escape(inputs.get("showtype", ""))} / {html.escape(",".join(inputs.get("gtypes", [])))}</p>
      </div>
      <div class="stamp">
        <div>快照时间</div>
        <strong>{html.escape(snapshot_time)}</strong>
      </div>
    </section>
    <section class="summary">
      {''.join(cards) or "<div class='summary-card'>暂无数据</div>"}
    </section>
    {''.join(sections)}
    <section class="raw-panel">
      <h3>原始快照摘要</h3>
      <pre>{raw_json}</pre>
    </section>
  </div>
</body>
</html>"""


def write_dashboard(path: str, payload: dict[str, Any], title: str) -> Path:
    dashboard_path = Path(path)
    dashboard_path.write_text(
        build_dashboard_html(payload, title),
        encoding="utf-8",
    )
    return dashboard_path


def offline_parse(xml_file: str) -> int:
    xml_text = Path(xml_file).read_text(encoding="utf-8")
    parsed = parse_game_list_xml(xml_text)
    print(json.dumps(parsed, ensure_ascii=False, indent=2))
    return 0


def poll_live(args: argparse.Namespace) -> int:
    body = read_text(
        inline_value=args.body,
        file_path=args.body_file,
        env_name="GET_GAME_LIST_BODY",
    )
    cookie = read_text(
        inline_value=args.cookie,
        file_path=args.cookie_file,
        env_name="GET_GAME_LIST_COOKIE",
    )
    if not body:
        print(
            "Missing request body. Use --body, --body-file, or GET_GAME_LIST_BODY.",
            file=sys.stderr,
        )
        return 2

    outdir = ensure_output_dir(args.output_dir)
    remaining = args.count
    gtypes = normalize_gtypes(args.gtypes)
    showtype = args.showtype
    rtype = args.rtype
    if rtype == "auto":
        rtype = SHOWTYPE_TO_RTYPE.get(showtype, "r")
    template = parse_form_body(body)

    while remaining != 0:
        try:
            payload, total_games, total_more, errors = collect_live_payload(
                args,
                cookie=cookie,
                template=template,
                gtypes=gtypes,
                showtype=showtype,
                rtype=rtype,
            )

            snapshot = write_multi_snapshot(outdir, payload)
            dashboard_path = None
            if args.dashboard_file:
                dashboard_path = write_dashboard(
                    args.dashboard_file,
                    payload,
                    args.dashboard_title,
                )
            summary = summarize_payload(
                payload,
                snapshot=snapshot,
                total_games=total_games,
                total_more=total_more,
                errors=errors,
                dashboard_path=dashboard_path,
            )
            print(json.dumps(summary, ensure_ascii=False))
        except urllib.error.URLError as exc:
            print(f"request failed: {exc}", file=sys.stderr)
        except ET.ParseError as exc:
            print(f"xml parse failed: {exc}", file=sys.stderr)
        except ValueError as exc:
            print(f"response parse failed: {exc}", file=sys.stderr)

        if remaining > 0:
            remaining -= 1
        if remaining != 0:
            time.sleep(args.interval)

    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Poll or parse the transform.php p=get_game_list XML feed.",
    )
    parser.add_argument("--url", default=DEFAULT_URL, help="Feed URL")
    parser.add_argument("--body", help="Inline POST body")
    parser.add_argument("--body-file", help="Path to file containing POST body")
    parser.add_argument("--cookie", help="Inline Cookie header")
    parser.add_argument("--cookie-file", help="Path to file containing Cookie header")
    parser.add_argument(
        "--gtypes",
        default="ft",
        help="Comma-separated sport codes, for example ft,bk,es,tn",
    )
    parser.add_argument(
        "--showtype",
        default="live",
        choices=("live", "today", "early"),
        help="Match list type to fetch",
    )
    parser.add_argument(
        "--rtype",
        default="auto",
        help="Request rtype. Use auto to map live->rb and others->r",
    )
    parser.add_argument(
        "--include-more",
        action="store_true",
        help="Also fetch p=get_game_more for every match in every feed",
    )
    parser.add_argument(
        "--more-filter",
        default="Main",
        help="Value for get_game_more filter, default Main",
    )
    parser.add_argument(
        "--more-delay",
        type=float,
        default=0.0,
        help="Delay in seconds between get_game_more requests",
    )
    parser.add_argument(
        "--xml-file",
        help="Parse a saved XML file once instead of polling live",
    )
    parser.add_argument(
        "--output-dir",
        default="live_snapshots",
        help="Directory to store live polling snapshots",
    )
    parser.add_argument(
        "--dashboard-file",
        help="Optional HTML dashboard output path",
    )
    parser.add_argument(
        "--dashboard-title",
        default="实时比赛看板",
        help="Dashboard page title",
    )
    parser.add_argument(
        "--interval",
        type=float,
        default=5.0,
        help="Polling interval in seconds",
    )
    parser.add_argument(
        "--count",
        type=int,
        default=1,
        help="Number of polling iterations. Use 0 for infinite loop.",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=15.0,
        help="Request timeout in seconds",
    )
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    if args.xml_file:
        return offline_parse(args.xml_file)
    return poll_live(args)


if __name__ == "__main__":
    raise SystemExit(main())
