#!/usr/bin/env python3
from __future__ import annotations

import argparse
import base64
import json
import subprocess
import tempfile
import urllib.request
from pathlib import Path


OMLX_BASE_URL = "http://127.0.0.1:8000/v1"
OMLX_API_KEY = "sk-1234"


def extract_first_frame(video_path: Path, output_dir: Path) -> Path:
    frame_path = output_dir / "frame.jpg"
    subprocess.run(
        ["ffmpeg", "-y", "-i", str(video_path), "-frames:v", "1", str(frame_path)],
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    return frame_path


def encode_image(path: Path) -> str:
    return base64.b64encode(path.read_bytes()).decode()


def post_chat(payload: dict, timeout: int) -> dict:
    req = urllib.request.Request(
        f"{OMLX_BASE_URL}/chat/completions",
        data=json.dumps(payload).encode(),
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {OMLX_API_KEY}",
        },
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode())


def build_messages_text(user_prompt: str) -> list[dict]:
    return [
        {"role": "system", "content": "你是一个严格遵守输出格式的助手。"},
        {"role": "user", "content": user_prompt},
    ]


def build_messages_vlm(user_prompt: str, image_path: Path) -> list[dict]:
    return [
        {"role": "system", "content": "你是直播画面分析助手。默认优先输出纯 JSON，不要解释。"},
        {
            "role": "user",
            "content": [
                {"type": "text", "text": user_prompt},
                {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64," + encode_image(image_path)}},
            ],
        },
    ]


def main() -> int:
    parser = argparse.ArgumentParser(description="Probe local OMLX models through the OpenAI-compatible API.")
    parser.add_argument("--model", required=True)
    parser.add_argument("--prompt", required=True)
    parser.add_argument("--image", default="")
    parser.add_argument("--video", default="")
    parser.add_argument("--max-tokens", type=int, default=256)
    parser.add_argument("--temperature", type=float, default=0.1)
    parser.add_argument("--timeout", type=int, default=180)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    with tempfile.TemporaryDirectory(prefix="omlx_probe_", dir="/tmp") as tmpdir:
        image_path: Path | None = None
        if args.image:
            image_path = Path(args.image)
        elif args.video:
            image_path = extract_first_frame(Path(args.video), Path(tmpdir))

        messages = (
            build_messages_vlm(args.prompt, image_path)
            if image_path is not None
            else build_messages_text(args.prompt)
        )
        payload = {
            "model": args.model,
            "messages": messages,
            "temperature": args.temperature,
            "max_tokens": args.max_tokens,
        }
        response = post_chat(payload, timeout=args.timeout)

        if args.json:
            print(json.dumps(response, ensure_ascii=False, indent=2))
            return 0

        choice = ((response.get("choices") or [{}])[0].get("message") or {})
        usage = response.get("usage") or {}
        print(f"model: {response.get('model')}")
        print(f"prompt_tokens: {usage.get('prompt_tokens')}")
        print(f"completion_tokens: {usage.get('completion_tokens')}")
        print("content:")
        print(choice.get("content", ""))
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
