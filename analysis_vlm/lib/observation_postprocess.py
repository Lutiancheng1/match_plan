"""Post-processing for 9B base model observation JSON output.

Handles:
1. Markdown code block wrapping (```json ... ```)
2. Truncated JSON (max_tokens cut off mid-object)
3. Field normalization (enum validation, type coercion)
"""
from __future__ import annotations

import json
import re

SCENE_TYPES = {"live_play", "replay", "scoreboard_focus", "crowd_or_bench", "stoppage", "unknown"}
VISIBILITY = {"clear", "partial", "hidden", "unknown"}
RISK_LEVELS = {"low", "medium", "high"}
TRADEABILITY = {"tradeable", "watch_only", "ignore"}
EVENT_LABELS = {
    "goal", "red_card", "penalty", "dangerous_attack", "celebration",
    "replay_sequence", "substitution", "injury_or_stoppage", "none",
}

# Default observation when parsing completely fails
FALLBACK_OBSERVATION = {
    "scene_type": "unknown",
    "score_detected": "",
    "match_clock_detected": "",
    "scoreboard_visibility": "unknown",
    "replay_risk": "high",
    "tradeability": "watch_only",
    "event_candidates": [],
    "confidence": 0.0,
    "explanation_short": "",
}


def extract_json_block(text: str) -> str | None:
    """Extract JSON object string from model output, handling markdown and noise."""
    text = text.strip()

    # Strip markdown code blocks
    if "```" in text:
        # Find content between ``` markers
        parts = text.split("```")
        for part in parts:
            stripped = part.strip()
            if stripped.startswith("json"):
                stripped = stripped[4:].strip()
            if stripped.startswith("{"):
                text = stripped
                break

    # Find outermost { ... }
    start = text.find("{")
    if start < 0:
        return None
    end = text.rfind("}")
    if end > start:
        return text[start : end + 1]

    # Truncated — no closing brace found, return from { to end
    return text[start:]


def repair_truncated_json(text: str) -> dict | None:
    """Attempt to repair truncated JSON by closing open brackets/braces."""
    if not text or not text.startswith("{"):
        return None

    # Try parsing as-is first
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    # Strategy: progressively strip trailing incomplete tokens and close brackets
    # Remove trailing incomplete string/number
    cleaned = re.sub(r',\s*"[^"]*$', "", text)  # trailing incomplete key
    cleaned = re.sub(r',\s*$', "", cleaned)  # trailing comma
    cleaned = re.sub(r':\s*"[^"]*$', ': ""', cleaned)  # trailing incomplete string value
    cleaned = re.sub(r':\s*\d+\.?\d*$', ": 0", cleaned)  # trailing incomplete number

    # Count open/close brackets
    open_braces = cleaned.count("{") - cleaned.count("}")
    open_brackets = cleaned.count("[") - cleaned.count("]")

    # Close them
    cleaned += "]" * max(0, open_brackets)
    cleaned += "}" * max(0, open_braces)

    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        pass

    # More aggressive: find the last valid top-level field and truncate there
    # Look for last complete "key": value pattern
    last_good = 0
    brace_depth = 0
    bracket_depth = 0
    in_string = False
    escape = False

    for i, ch in enumerate(text):
        if escape:
            escape = False
            continue
        if ch == "\\":
            escape = True
            continue
        if ch == '"':
            in_string = not in_string
            continue
        if in_string:
            continue
        if ch == "{":
            brace_depth += 1
        elif ch == "}":
            brace_depth -= 1
            if brace_depth == 0:
                last_good = i + 1
        elif ch == "[":
            bracket_depth += 1
        elif ch == "]":
            bracket_depth -= 1
        elif ch == "," and brace_depth == 1 and bracket_depth == 0:
            last_good = i

    if last_good > 0:
        truncated = text[:last_good].rstrip(",").rstrip()
        truncated += "}" * max(0, truncated.count("{") - truncated.count("}"))
        try:
            return json.loads(truncated)
        except json.JSONDecodeError:
            pass

    return None


def _normalize_enum(value: object, allowed: set[str], fallback: str) -> str:
    """Normalize a value to one of the allowed enum strings."""
    text = str(value or "").strip().lower()
    # Try exact match
    if text in allowed:
        return text
    # Try fuzzy match (handle common typos like 'crow_or_bench' -> 'crowd_or_bench')
    for candidate in allowed:
        if text and candidate.startswith(text[:4]):
            return candidate
    return fallback


def normalize_observation(raw: dict) -> dict:
    """Normalize a parsed observation dict to enforce schema constraints."""
    obs = dict(FALLBACK_OBSERVATION)  # start with defaults

    obs["scene_type"] = _normalize_enum(raw.get("scene_type"), SCENE_TYPES, "unknown")
    obs["score_detected"] = str(raw.get("score_detected") or "").strip()
    obs["match_clock_detected"] = str(raw.get("match_clock_detected") or "").strip()
    obs["scoreboard_visibility"] = _normalize_enum(raw.get("scoreboard_visibility"), VISIBILITY, "unknown")
    obs["replay_risk"] = _normalize_enum(raw.get("replay_risk"), RISK_LEVELS, "high")
    obs["tradeability"] = _normalize_enum(raw.get("tradeability"), TRADEABILITY, "watch_only")

    # Normalize event_candidates
    candidates = raw.get("event_candidates") or []
    if isinstance(candidates, list):
        clean_candidates = []
        for c in candidates:
            if isinstance(c, dict):
                label = _normalize_enum(c.get("label"), EVENT_LABELS, "none")
                confidence = c.get("confidence", 0.0)
                try:
                    confidence = float(confidence)
                except (ValueError, TypeError):
                    confidence = 0.0
                clean_candidates.append({"label": label, "confidence": round(confidence, 2)})
        obs["event_candidates"] = clean_candidates

    try:
        obs["confidence"] = round(float(raw.get("confidence", 0.0)), 2)
    except (ValueError, TypeError):
        obs["confidence"] = 0.0

    obs["explanation_short"] = str(raw.get("explanation_short") or "").strip()[:200]

    return obs


def parse_model_output(text: str) -> dict:
    """Parse model output text into a normalized observation dict.

    Always returns a valid observation dict — never raises.
    Returns FALLBACK_OBSERVATION if parsing completely fails.
    """
    json_str = extract_json_block(text)
    if not json_str:
        return dict(FALLBACK_OBSERVATION)

    # Try direct parse
    try:
        raw = json.loads(json_str)
        return normalize_observation(raw)
    except json.JSONDecodeError:
        pass

    # Try repair truncated JSON
    raw = repair_truncated_json(json_str)
    if raw:
        return normalize_observation(raw)

    return dict(FALLBACK_OBSERVATION)
