"""Live frame observation client for oMLX 9B base model.

Usage:
    from analysis_vlm.lib.live_observer import LiveObserver

    observer = LiveObserver()
    result = observer.observe_frame("/path/to/frame.jpg")
    print(result["observation"])  # normalized dict
    print(result["latency_ms"])
"""
from __future__ import annotations

import base64
import json
import time
import urllib.request
from pathlib import Path

from analysis_vlm.lib.observation_postprocess import parse_model_output

OMLX_BASE_URL = "http://127.0.0.1:8000/v1"
OMLX_API_KEY = "sk-1234"
DEFAULT_MODEL = "Qwen3.5-VL-9B-8bit-MLX-CRACK"

OBSERVATION_PROMPT = (
    "请只输出纯JSON，不要解释，也不要使用 markdown 代码块。"
    "字段固定为 scene_type, score_detected, match_clock_detected, scoreboard_visibility, "
    "replay_risk, tradeability, event_candidates, confidence, explanation_short。"
    "scene_type 只能是 live_play, replay, scoreboard_focus, crowd_or_bench, stoppage, unknown 之一。"
    "score_detected 必须是类似 1-0 的字符串；看不清时输出空字符串。"
    "match_clock_detected 必须是类似 45:00 的字符串；看不清时输出空字符串。"
    "scoreboard_visibility 只能是 clear, partial, hidden, unknown。"
    "replay_risk 只能是 low, medium, high。"
    "tradeability 只能是 tradeable, watch_only, ignore。"
    "event_candidates 必须是数组；每个元素是对象，字段固定为 label 和 confidence。"
    "label 只能是 goal, red_card, penalty, dangerous_attack, celebration, "
    "replay_sequence, substitution, injury_or_stoppage, none 之一。"
)

SYSTEM_PROMPT = "你是直播画面分析助手。严格只输出纯 JSON，不要解释。"


class LiveObserver:
    """Stateless observer that sends frames to oMLX and returns normalized observations."""

    def __init__(
        self,
        base_url: str = OMLX_BASE_URL,
        api_key: str = OMLX_API_KEY,
        model: str = DEFAULT_MODEL,
        max_tokens: int = 512,
        timeout: int = 30,
    ):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.model = model
        self.max_tokens = max_tokens
        self.timeout = timeout

    def _post_chat(self, payload: dict) -> dict:
        req = urllib.request.Request(
            f"{self.base_url}/chat/completions",
            data=json.dumps(payload).encode(),
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self.api_key}",
            },
        )
        with urllib.request.urlopen(req, timeout=self.timeout) as resp:
            return json.loads(resp.read().decode())

    def observe_frame(self, image_path: str | Path) -> dict:
        """Observe a single frame and return normalized observation.

        Returns:
            {
                "observation": {...},  # normalized observation dict
                "raw_output": str,     # raw model output
                "latency_ms": float,
                "success": bool,
                "error": str | None,
            }
        """
        image_path = Path(image_path)
        if not image_path.exists():
            return {
                "observation": None,
                "raw_output": "",
                "latency_ms": 0,
                "success": False,
                "error": f"Image not found: {image_path}",
            }

        img_b64 = base64.b64encode(image_path.read_bytes()).decode()

        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": OBSERVATION_PROMPT},
                        {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{img_b64}"}},
                    ],
                },
            ],
            "temperature": 0.1,
            "max_tokens": self.max_tokens,
        }

        t0 = time.perf_counter()
        try:
            response = self._post_chat(payload)
            raw_output = (
                ((response.get("choices") or [{}])[0].get("message") or {}).get("content", "")
            )
            elapsed_ms = round((time.perf_counter() - t0) * 1000, 1)
            observation = parse_model_output(raw_output)
            return {
                "observation": observation,
                "raw_output": raw_output,
                "latency_ms": elapsed_ms,
                "success": True,
                "error": None,
            }
        except Exception as exc:
            elapsed_ms = round((time.perf_counter() - t0) * 1000, 1)
            return {
                "observation": None,
                "raw_output": "",
                "latency_ms": elapsed_ms,
                "success": False,
                "error": str(exc),
            }

    def observe_bytes(self, image_bytes: bytes, mime_type: str = "image/jpeg") -> dict:
        """Observe from raw bytes (e.g., screenshot buffer)."""
        img_b64 = base64.b64encode(image_bytes).decode()

        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": OBSERVATION_PROMPT},
                        {"type": "image_url", "image_url": {"url": f"data:{mime_type};base64,{img_b64}"}},
                    ],
                },
            ],
            "temperature": 0.1,
            "max_tokens": self.max_tokens,
        }

        t0 = time.perf_counter()
        try:
            response = self._post_chat(payload)
            raw_output = (
                ((response.get("choices") or [{}])[0].get("message") or {}).get("content", "")
            )
            elapsed_ms = round((time.perf_counter() - t0) * 1000, 1)
            observation = parse_model_output(raw_output)
            return {
                "observation": observation,
                "raw_output": raw_output,
                "latency_ms": elapsed_ms,
                "success": True,
                "error": None,
            }
        except Exception as exc:
            elapsed_ms = round((time.perf_counter() - t0) * 1000, 1)
            return {
                "observation": None,
                "raw_output": "",
                "latency_ms": elapsed_ms,
                "success": False,
                "error": str(exc),
            }

    def health_check(self) -> bool:
        """Check if oMLX is reachable and model is loaded."""
        try:
            req = urllib.request.Request(
                f"{self.base_url}/models",
                headers={"Authorization": f"Bearer {self.api_key}"},
            )
            with urllib.request.urlopen(req, timeout=5) as resp:
                data = json.loads(resp.read().decode())
                models = [m["id"] for m in data.get("data", [])]
                return self.model in models
        except Exception:
            return False
