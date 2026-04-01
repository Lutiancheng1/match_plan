#!/usr/bin/env python3
"""
Quick local comparison for alias translation candidates.

Compares:
- oMLX chat models (for example Qwen3-4B / GLM-4.7-Flash)
- local NLLB direct translation

This is meant for spot-checking unknown team/league names before we decide
which model should handle the "unknown term" layer.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import time
import urllib.request
from pathlib import Path

import torch
from transformers import AutoModelForSeq2SeqLM, NllbTokenizer


SCRIPT_DIR = Path(__file__).resolve().parent
RUN_AUTO_CAPTURE_PATH = SCRIPT_DIR / "run_auto_capture.py"

DEFAULT_CASES = [
    {"type": "team", "term": "Fluminense", "src": "eng_Latn", "tgt": "zho_Hans"},
    {"type": "team", "term": "Bahia W", "src": "eng_Latn", "tgt": "zho_Hans"},
    {"type": "team", "term": "Sao Paulo", "src": "eng_Latn", "tgt": "zho_Hans"},
    {"type": "team", "term": "Maritimo U23", "src": "eng_Latn", "tgt": "zho_Hans"},
    {"type": "team", "term": "Thailand U20 W", "src": "eng_Latn", "tgt": "zho_Hans"},
    {"type": "league", "term": "Botola Pro", "src": "eng_Latn", "tgt": "zho_Hans"},
    {"type": "league", "term": "Ligue 1", "src": "eng_Latn", "tgt": "zho_Hans"},
    {"type": "league", "term": "国际友谊赛", "src": "zho_Hans", "tgt": "eng_Latn"},
    {"type": "league", "term": "欧洲女子冠军联赛", "src": "zho_Hans", "tgt": "eng_Latn"},
    {"type": "league", "term": "哥伦比亚甲组联赛", "src": "zho_Hans", "tgt": "eng_Latn"},
    {"type": "league", "term": "乌拉圭甲组联赛", "src": "zho_Hans", "tgt": "eng_Latn"},
    {"type": "team", "term": "日本女足", "src": "zho_Hans", "tgt": "eng_Latn"},
]


def load_run_auto_capture():
    spec = importlib.util.spec_from_file_location("run_auto_capture", RUN_AUTO_CAPTURE_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def build_omlx_prompt(term: str) -> str:
    return (
        "You normalize football team and competition names for cross-language betting-feed matching.\n"
        "Return strict JSON only with schema: {\"items\":[{\"term\":\"...\",\"aliases\":[\"...\"]}]}\n"
        "Rules:\n"
        "- Return the single best opposite-language alias for each term.\n"
        "- Preserve exact entity identity.\n"
        "- Preserve markers exactly: U17 U19 U20 U21 U23 W Women.\n"
        "- If input is English or other Latin script, output a Chinese alias in Chinese characters.\n"
        "- If input is Chinese, output an English alias in Latin letters.\n"
        "- Prefer short bookmaker-style names, not explanations.\n"
        "- If unsure, use an empty aliases array.\n"
        f"Terms: {json.dumps([term], ensure_ascii=False)}"
    )


def query_omlx_model(base_url: str, api_key: str, model_name: str, term: str) -> str:
    body = {
        "model": model_name,
        "temperature": 0,
        "max_tokens": 256,
        "messages": [
            {
                "role": "system",
                "content": "You are a football betting-feed alias normalizer. Reply with strict JSON only and never include reasoning.",
            },
            {"role": "user", "content": build_omlx_prompt(term)},
        ],
    }
    request = urllib.request.Request(
        f"{base_url}/chat/completions",
        data=json.dumps(body).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=120) as response:
        payload = json.loads(response.read().decode("utf-8"))
    content = payload["choices"][0]["message"]["content"]
    parsed = json.loads(content)
    items = parsed.get("items") or []
    if not items:
        return ""
    aliases = items[0].get("aliases") or []
    return str(aliases[0]).strip() if aliases else ""


def heuristic_nllb_input(case_type: str, term: str) -> str:
    text = term.strip()
    if case_type == "team":
        if text.endswith(" W"):
            return f"{text[:-2]} Women FC"
        if " U23" in text or " U21" in text or " U20" in text:
            return text
        if all(ord(ch) < 128 for ch in text) and " " in text and not text.endswith("FC"):
            return f"{text} FC"
    return text


class NllbRunner:
    def __init__(self, model_dir: Path):
        self.model_dir = model_dir
        self.device = "mps" if torch.backends.mps.is_available() else "cpu"
        self.tokenizer = NllbTokenizer.from_pretrained(model_dir, local_files_only=True)
        self.model = AutoModelForSeq2SeqLM.from_pretrained(
            model_dir,
            local_files_only=True,
            dtype=torch.float16 if self.device == "mps" else torch.float32,
            low_cpu_mem_usage=True,
        ).to(self.device)
        self.model.eval()

    def translate(self, text: str, src_lang: str, tgt_lang: str) -> str:
        self.tokenizer.src_lang = src_lang
        self.tokenizer.tgt_lang = tgt_lang
        inputs = self.tokenizer(text, return_tensors="pt")
        inputs = {key: value.to(self.device) for key, value in inputs.items()}
        with torch.inference_mode():
            output = self.model.generate(
                **inputs,
                forced_bos_token_id=self.tokenizer.convert_tokens_to_ids(tgt_lang),
                max_new_tokens=32,
                num_beams=4,
            )
        return self.tokenizer.batch_decode(output, skip_special_tokens=True)[0].strip()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--models", nargs="*", default=["Qwen3-4B-Instruct-2507-8bit"])
    parser.add_argument(
        "--nllb-dir",
        default=str(Path.home() / ".cache" / "huggingface" / "nllb-200-distilled-1.3B"),
    )
    args = parser.parse_args()

    run_auto_capture = load_run_auto_capture()
    provider = run_auto_capture.get_openclaw_translation_provider()
    base_url = provider["base_url"]
    api_key = provider["api_key"]

    nllb = NllbRunner(Path(args.nllb_dir))

    print(f"NLLB device={nllb.device}")
    for case in DEFAULT_CASES:
        print(f"\n[{case['type']}] {case['term']}")
        for model_name in args.models:
            started = time.time()
            try:
                alias = query_omlx_model(base_url, api_key, model_name, case["term"])
            except Exception as exc:
                alias = f"<ERR {exc}>"
            print(f"  {model_name}: {alias} ({time.time() - started:.2f}s)")

        nllb_input = heuristic_nllb_input(case["type"], case["term"])
        started = time.time()
        try:
            alias = nllb.translate(nllb_input, case["src"], case["tgt"])
        except Exception as exc:
            alias = f"<ERR {exc}>"
        suffix = "" if nllb_input == case["term"] else f" [input={nllb_input}]"
        print(f"  NLLB: {alias}{suffix} ({time.time() - started:.2f}s)")


if __name__ == "__main__":
    main()
