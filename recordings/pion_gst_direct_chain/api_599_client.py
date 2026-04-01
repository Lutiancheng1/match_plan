#!/usr/bin/env python3
"""Embedded 599 API client used by the recorder pipeline.

This file vendors the subset of the 599 client that the recorder relies on,
so the recording project does not depend on the sibling `599_project/` repo
being present in the workspace at runtime.
"""
from __future__ import annotations

import base64
import gzip
import hashlib
import json
import time
from typing import Any

import requests
from Crypto.Cipher import AES
from Crypto.Util.Padding import unpad

FB_URL = "https://fb-i.599.com"
ODDS_URL = "https://papapa.599.com"
WJJ_MD5 = hashlib.md5(b"wjj").hexdigest()
AES_KEY = b"777db0c19edfaace"
AES_IV = b"9876543210599311"

DEFAULT_PARAMS = {
    "appType": "1",
    "appno": "11",
    "channelNumber": "appStore",
    "comId": "8",
    "deviceId": "5F198AB462FF424F9D288F340369F625",
    "deviceToken": "121c83f76176e81113b",
    "idfa": "5FD3293A-B807-4FAC-B819-2CFE632B8294",
    "lang": "zh",
    "loginToken": "",
    "pushPlatType": "3",
    "timeZone": "8",
    "version": "317",
}

HEADERS = {
    "User-Agent": "599/317 CFNetwork/1399 Darwin/22.1.0",
    "Accept": "*/*",
    "Accept-Language": "zh-Hans-CN;q=1",
}


def generate_sign(path: str, params: dict) -> str:
    content = path
    for key in sorted(params.keys()):
        if key == "sign":
            continue
        content += key + str(params[key])
    content += WJJ_MD5
    return hashlib.md5(content.encode()).hexdigest() + "00"


def decrypt_response(data_b64: str) -> Any:
    cipher = AES.new(AES_KEY, AES.MODE_CBC, AES_IV)
    encrypted = base64.b64decode(data_b64)
    decrypted = unpad(cipher.decrypt(encrypted), AES.block_size)
    text = decrypted.decode("utf-8")
    if text.startswith("H4sI"):
        raw = gzip.decompress(base64.b64decode(text))
        return json.loads(raw.decode("utf-8"))
    return json.loads(text)


def api_request(
    path: str,
    extra_params: dict | None = None,
    *,
    method: str = "GET",
    base_url: str | None = None,
    decrypt: bool = False,
) -> dict:
    base = base_url or FB_URL
    params = dict(DEFAULT_PARAMS)
    params["st"] = str(int(time.time() * 1000))
    if extra_params:
        params.update(extra_params)
    params["sign"] = generate_sign(path, params)

    url = f"{base}{path}"
    if method == "GET":
        response = requests.get(url, params=params, headers=HEADERS, timeout=15)
    else:
        response = requests.post(url, data=params, headers=HEADERS, timeout=15)

    result = response.json()
    if decrypt and "data" in result and isinstance(result["data"], str):
        try:
            result["data"] = decrypt_response(result["data"])
        except Exception as exc:
            result["_decrypt_error"] = str(exc)
            result["_raw_data"] = result["data"][:200]
    return result


def get_match_list():
    return api_request("/footballapi/core/matchlist/jacky")


def get_ceaseless_matches(date: str = ""):
    return api_request("/footballapi/core/matchlist/v1/ceaseless", {"date": date})


def get_finished_matches(date: str = ""):
    return api_request("/footballapi/core/matchlist/v1/result", {"date": date})


def get_match_info(third_id: str):
    return api_request(
        "/footballapi/core/details/v1/matchinfo",
        {"thirdId": third_id},
        decrypt=True,
    )


def get_match_stats(third_id: str):
    return api_request("/footballapi/core/details/v1/statistics", {"thirdId": third_id})


def get_match_intel(third_id: str):
    return api_request("/footballapi/core/details/matchintel", {"thirdId": third_id})


def get_live_text(third_id: str, start_msg_id: str = "0", total: str = "0"):
    return api_request(
        "/footballapi/core/details/footBallMatch.findLiveText.do",
        {"thirdId": third_id, "startMsgId": start_msg_id, "total": total},
        decrypt=True,
    )


def get_all_live_text(third_id: str):
    info = get_match_info(third_id)
    data = info.get("data", {})
    msgs = data.get("matchInfo", {}).get("matchLive", [])
    if not msgs:
        return []

    all_msgs = list(msgs)
    while True:
        last_id = all_msgs[-1].get("msgId")
        if not last_id:
            break
        page = get_live_text(third_id, str(last_id), str(len(all_msgs)))
        new_msgs = page.get("data", [])
        if not isinstance(new_msgs, list) or not new_msgs:
            break
        all_msgs.extend(new_msgs)

    return all_msgs


def get_odds(third_id: str):
    return api_request("/oddscenter/core/instant/odds", {"thirdId": third_id}, base_url=ODDS_URL)


def get_video_urls(third_id: str):
    return api_request("/footballapi/core/video/urlSet", {"thirdId": third_id})


def get_line(match_id: str):
    return api_request("/footballapi/core/line", {"matchId": match_id})
