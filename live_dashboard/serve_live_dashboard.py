#!/usr/bin/env python3
"""Run a local live dashboard backed by the get_game_list poller."""

from __future__ import annotations

import argparse
import json
import os
import errno
import sys
import threading
import time
from datetime import datetime
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import sqlite3
import urllib.parse
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import poll_get_game_list as poller
import auto_login
import db_store


APP_HTML = """<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>__TITLE__</title>
  <style>
    :root {{
      --bg: #09111d;
      --panel: rgba(18, 29, 48, 0.94);
      --panel-2: rgba(12, 21, 35, 0.9);
      --line: #23415f;
      --text: #eef4fb;
      --muted: #9fb2cb;
      --accent: #4fd1ff;
      --accent-2: #ffd166;
      --good: #78e08f;
      --bad: #ff7b72;
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      font-family: ui-sans-serif, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      background:
        radial-gradient(circle at top left, rgba(79,209,255,0.16), transparent 22%),
        radial-gradient(circle at top right, rgba(255,209,102,0.09), transparent 20%),
        linear-gradient(180deg, #060d18 0%, #0b1524 100%);
      color: var(--text);
    }}
    .wrap {{
      width: min(1680px, calc(100vw - 32px));
      margin: 0 auto;
      padding: 24px 0 48px;
    }}
    .hero {{
      display: grid;
      grid-template-columns: 1.3fr 0.9fr;
      gap: 16px;
      margin-bottom: 18px;
    }}
    .hero-card, .status-card, .summary-card, .panel, .match-card {{
      background: linear-gradient(180deg, rgba(20,32,53,0.96), rgba(9,17,29,0.94));
      border: 1px solid var(--line);
      border-radius: 18px;
      box-shadow: 0 20px 48px rgba(0,0,0,0.28);
    }}
    .hero-card, .status-card {{
      padding: 20px;
    }}
    h1, h2, h3, p {{
      margin: 0;
    }}
    .hero-card h1 {{
      font-size: 34px;
      letter-spacing: -0.03em;
    }}
    .hero-card p {{
      margin-top: 10px;
      color: var(--muted);
      line-height: 1.55;
    }}
    .status-grid {{
      display: grid;
      grid-template-columns: repeat(2, minmax(0, 1fr));
      gap: 12px;
      margin-top: 14px;
    }}
    .status-item {{
      background: rgba(255,255,255,0.02);
      border: 1px solid rgba(35,65,95,0.7);
      border-radius: 14px;
      padding: 12px;
    }}
    .label {{
      color: var(--muted);
      font-size: 12px;
    }}
    .value {{
      margin-top: 6px;
      font-size: 15px;
      font-weight: 700;
      word-break: break-word;
    }}
    .summary {{
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(150px, 1fr));
      gap: 12px;
      margin-bottom: 22px;
    }}
    .summary-card {{
      padding: 16px;
    }}
    .summary-card .name {{
      color: var(--muted);
      font-size: 12px;
    }}
    .summary-card .count {{
      margin-top: 8px;
      font-size: 34px;
      font-weight: 800;
    }}
    .summary-card .meta {{
      margin-top: 8px;
      color: var(--accent);
      font-size: 13px;
    }}
    .toolbar {{
      display: grid;
      gap: 12px;
      margin-bottom: 20px;
    }}
    .toolbar-row {{
      display: flex;
      gap: 10px;
      flex-wrap: wrap;
      align-items: center;
    }}
    .tool {{
      background: rgba(10,18,31,0.8);
      color: var(--muted);
      border: 1px solid rgba(35,65,95,0.7);
      border-radius: 999px;
      padding: 8px 12px;
      font-size: 13px;
    }}
    .tool-input {{
      min-width: 260px;
      padding: 10px 14px;
      border-radius: 999px;
      border: 1px solid rgba(35,65,95,0.8);
      background: rgba(10,18,31,0.85);
      color: var(--text);
      outline: none;
    }}
    .tool-button {{
      padding: 9px 14px;
      border-radius: 999px;
      border: 1px solid rgba(35,65,95,0.8);
      background: rgba(10,18,31,0.9);
      color: var(--text);
      cursor: pointer;
    }}
    .tool-button.active, .summary-card.active {{
      border-color: var(--accent);
      box-shadow: inset 0 0 0 1px rgba(79,209,255,0.25);
    }}
    .summary-card {{
      cursor: pointer;
    }}
    .sports {{
      display: grid;
      gap: 22px;
    }}
    .sport {{
      display: grid;
      gap: 12px;
    }}
    .sport-head {{
      display: flex;
      justify-content: space-between;
      align-items: baseline;
      gap: 12px;
    }}
    .sport-head h2 {{
      font-size: 24px;
    }}
    .sport-head .meta {{
      color: var(--accent-2);
      font-size: 14px;
    }}
    .sport-grid {{
      display: grid;
      grid-template-columns: 300px 1fr;
      gap: 12px;
    }}
    .panel {{
      padding: 16px;
    }}
    .league-row {{
      display: flex;
      justify-content: space-between;
      gap: 10px;
      padding: 9px 0;
      border-bottom: 1px solid rgba(35,65,95,0.55);
      color: var(--muted);
    }}
    .league-row strong {{
      color: var(--text);
    }}
    .matches {{
      display: grid;
      grid-template-columns: repeat(auto-fill, minmax(320px, 1fr));
      gap: 10px;
    }}
    .match-card {{
      padding: 14px;
    }}
    .match-top {{
      display: flex;
      justify-content: space-between;
      gap: 12px;
      align-items: start;
    }}
    .league {{
      color: var(--accent);
      font-size: 12px;
      margin-bottom: 4px;
    }}
    .teams {{
      font-size: 16px;
      font-weight: 700;
      line-height: 1.35;
    }}
    .score {{
      font-size: 24px;
      font-weight: 800;
      color: var(--accent-2);
      white-space: nowrap;
    }}
    .meta-row, .odds-row, .detail-row {{
      margin-top: 10px;
      display: flex;
      gap: 10px 12px;
      flex-wrap: wrap;
    }}
    .meta-row {{
      color: var(--muted);
      font-size: 12px;
    }}
    .odds-row {{
      color: var(--text);
      font-size: 13px;
    }}
    .detail-row {{
      color: var(--good);
      font-size: 12px;
    }}
    .category-row {{
      margin-top: 10px;
      display: flex;
      gap: 8px;
      flex-wrap: wrap;
    }}
    .category-chip {{
      padding: 5px 9px;
      border-radius: 999px;
      font-size: 12px;
      color: var(--text);
      border: 1px solid rgba(79,209,255,0.24);
      background: rgba(79,209,255,0.08);
    }}
    .category-chip strong {{
      color: var(--accent-2);
      margin-left: 4px;
    }}
    details.market-detail {{
      margin-top: 10px;
      border-top: 1px solid rgba(35,65,95,0.55);
      padding-top: 10px;
    }}
    details.market-detail summary {{
      cursor: pointer;
      color: var(--accent);
      font-size: 12px;
      user-select: none;
    }}
    .category-preview {{
      margin-top: 10px;
      display: grid;
      gap: 8px;
    }}
    .category-preview-item {{
      border: 1px solid rgba(35,65,95,0.55);
      border-radius: 12px;
      padding: 8px 10px;
      background: rgba(10,18,31,0.46);
    }}
    .category-preview-head {{
      display: flex;
      justify-content: space-between;
      gap: 10px;
      font-size: 12px;
      color: var(--accent-2);
    }}
    .category-preview-body {{
      margin-top: 6px;
      color: var(--muted);
      font-size: 12px;
      line-height: 1.45;
    }}
    .empty, .error {{
      padding: 14px 0;
      color: var(--muted);
    }}
    .error {{
      color: var(--bad);
    }}
    .raw {{
      margin-top: 22px;
    }}
    .hidden {{
      display: none;
    }}
    pre {{
      margin: 0;
      white-space: pre-wrap;
      word-break: break-word;
      max-height: 320px;
      overflow: auto;
      color: #bed1eb;
      font-size: 12px;
    }}
    @media (max-width: 980px) {{
      .hero, .sport-grid {{
        grid-template-columns: 1fr;
      }}
    }}
  </style>
</head>
<body>
  <div class="wrap">
    <section class="hero">
      <div class="hero-card">
        <h1>__TITLE__</h1>
        <p>这个页面会持续从本地服务拉取最新快照。后台轮询线程会一直更新数据文件，前端每 __REFRESH_SECONDS__ 秒同步一次，不需要重新打开页面。</p>
      </div>
      <div class="status-card">
        <h2>运行状态</h2>
        <div class="status-grid" id="status-grid"></div>
      </div>
    </section>
    <section class="summary" id="summary"></section>
    <section class="toolbar">
      <div class="toolbar-row" id="toolbar-meta"></div>
      <div class="toolbar-row">
        <input id="search-input" class="tool-input" type="search" placeholder="搜索球队、联赛、ECID">
        <button id="refresh-button" class="tool-button" type="button">立即刷新</button>
        <button id="reset-button" class="tool-button" type="button">重置筛选</button>
        <button id="toggle-raw-button" class="tool-button" type="button">显示原始 JSON</button>
      </div>
    </section>
    <section class="sports" id="sports"></section>
    <section class="panel raw hidden" id="raw-panel">
      <h3>最近一次原始快照</h3>
      <pre id="raw-json">等待数据...</pre>
    </section>
  </div>
  <script>
    const sportLabels = {{
      FT: "足球",
      BK: "篮球",
      ES: "电子竞技",
      TN: "网球",
      VB: "排球",
      BM: "羽毛球",
      TT: "乒乓球",
      BS: "棒球",
      SK: "斯诺克",
      OP: "其他"
    }};
    const categoryLabels = {{
      base: "基础信息",
      scoreboard: "比分拆分",
      main: "主盘口",
      handicap: "让球",
      totals: "大小/总分",
      team_totals: "球队大小",
      moneyline: "独赢",
      odd_even: "单双",
      halves: "半场",
      periods: "节/盘/局",
      points: "分点玩法",
      goals: "进球相关",
      corners: "角球",
      bookings: "牌数",
      intervals: "区间玩法",
      rmix_ou: "让球/大小混合",
      specials: "特殊玩法"
    }};
    const refreshMs = __REFRESH_MS__;
    const uiState = {{
      selectedSport: "ALL",
      query: "",
      showRaw: false,
    }};
    let latestStatus = null;
    let latestData = null;

    function escapeHtml(value) {{
      return String(value ?? "")
        .replaceAll("&", "&amp;")
        .replaceAll("<", "&lt;")
        .replaceAll(">", "&gt;")
        .replaceAll('"', "&quot;")
        .replaceAll("'", "&#39;");
    }}

    function sportLabel(code) {{
      return sportLabels[code] || code;
    }}

    function buildOddsLine(fields) {{
      const parts = [];
      const handicap = [fields.RATIO_RE, fields.IOR_REH, fields.IOR_REC].filter(Boolean);
      if (handicap.length) parts.push(`让球 ${handicap.join(" ")}`);
      const total = [fields.RATIO_ROUO || fields.RATIO_ROUU, fields.IOR_ROUH, fields.IOR_ROUC].filter(Boolean);
      if (total.length) parts.push(`大小 ${total.join(" ")}`);
      return parts;
    }}

    function buildHalfOddsLine(fields) {{
      const parts = [];
      const handicap = [fields.RATIO_HRE, fields.IOR_HREH, fields.IOR_HREC].filter(Boolean);
      if (handicap.length) parts.push(`半场让球 ${handicap.join(" ")}`);
      const total = [fields.RATIO_HROUO || fields.RATIO_HROUU, fields.IOR_HROUH, fields.IOR_HROUC].filter(Boolean);
      if (total.length) parts.push(`半场大小 ${total.join(" ")}`);
      return parts;
    }}

    function buildMoreLine(moreFields) {{
      if (!moreFields) return "";
      const swCount = Object.entries(moreFields).filter(([key, value]) => key.startsWith("sw_") && value === "Y").length;
      const parts = [];
      if (swCount) parts.push(`全盘口开关 ${swCount} 项`);
      if (moreFields.ratio_rouho || moreFields.ior_ROUHO || moreFields.ior_ROUHU) {{
        parts.push(`主队大小 ${[moreFields.ratio_rouho || moreFields.ratio_rouhu, moreFields.ior_ROUHO, moreFields.ior_ROUHU].filter(Boolean).join(" ")}`);
      }}
      if (moreFields.ratio_rouco || moreFields.ior_ROUCO || moreFields.ior_ROUCU) {{
        parts.push(`客队大小 ${[moreFields.ratio_rouco || moreFields.ratio_roucu, moreFields.ior_ROUCO, moreFields.ior_ROUCU].filter(Boolean).join(" ")}`);
      }}
      return parts.join(" · ");
    }}

    function categoryLabel(name) {{
      return categoryLabels[name] || name;
    }}

    function currentDetail(game) {{
      return game.detail || null;
    }}

    function currentCategoryCounts(game) {{
      const detail = currentDetail(game);
      return (detail && detail.category_counts) || game.category_counts || {{}};
    }}

    function currentCategories(game) {{
      const detail = currentDetail(game);
      return (detail && detail.categories) || game.categories || {{}};
    }}

    function renderCategoryChips(game) {{
      const counts = currentCategoryCounts(game);
      const entries = Object.entries(counts);
      if (!entries.length) return "";
      return entries
        .sort((a, b) => b[1] - a[1])
        .slice(0, 6)
        .map(([name, count]) => `<span class="category-chip">${{escapeHtml(categoryLabel(name))}}<strong>${{escapeHtml(count)}}</strong></span>`)
        .join("");
    }}

    function summarizeValues(values) {{
      if (!values) return "";
      const entries = Object.entries(values).filter(([, value]) => value !== "" && value !== null && value !== undefined);
      return entries
        .slice(0, 4)
        .map(([key, value]) => `${{key}}=${{value}}`)
        .join(" · ");
    }}

    function formatWallClock(isoString, timeZone) {{
      if (!isoString) return "";
      const date = new Date(isoString);
      if (Number.isNaN(date.getTime())) return isoString;
      return new Intl.DateTimeFormat("zh-CN", {{
        timeZone,
        year: "numeric",
        month: "2-digit",
        day: "2-digit",
        hour: "2-digit",
        minute: "2-digit",
        second: "2-digit",
        hour12: false,
      }}).format(date);
    }}

    function formatTimestampLabel(isoString) {{
      if (!isoString) return "暂无";
      const localLabel = formatWallClock(isoString, Intl.DateTimeFormat().resolvedOptions().timeZone || "America/Los_Angeles");
      const beijingLabel = formatWallClock(isoString, "Asia/Shanghai");
      return `${{localLabel}}（本地） / ${{beijingLabel}}（北京时间）`;
    }}

    function formatSnapshotMeaning(isoString) {{
      if (!isoString) return "暂无";
      return `${{formatTimestampLabel(isoString)}} · 表示后台抓取快照时间，不是比赛时间`;
    }}

    function renderCategoryPreview(game) {{
      const categories = currentCategories(game);
      const entries = Object.entries(categories);
      if (!entries.length) return "";
      return entries
        .slice(0, 5)
        .map(([name, slot]) => {{
          const firstItem = (slot.items || [])[0];
          const summary = firstItem ? summarizeValues(firstItem.values || {{}}) : "";
          return `
            <div class="category-preview-item">
              <div class="category-preview-head">
                <span>${{escapeHtml(categoryLabel(name))}}</span>
                <span>${{escapeHtml((slot.items || []).length)}} 项</span>
              </div>
              <div class="category-preview-body">${{escapeHtml(summary || "该分类已接入，展开 raw JSON 可看全部字段")}}</div>
            </div>
          `;
        }})
        .join("");
    }}

    function renderStatus(status, data) {{
      const totalGames = Object.values((data && data.feeds) || {{}}).reduce((sum, feed) => sum + ((feed.parsed && feed.parsed.games && feed.parsed.games.length) || 0), 0);
      const rows = [
        ["轮询状态", status.running ? "运行中" : "未运行"],
        ["最后成功", formatTimestampLabel(status.last_success)],
        ["最后错误", status.last_error || "无"],
        ["总比赛数", totalGames],
        ["监听地址", status.url || location.origin],
        ["输出目录", status.output_dir || ""]
      ];
      document.getElementById("status-grid").innerHTML = rows.map(([label, value]) => `
        <div class="status-item">
          <div class="label">${{escapeHtml(label)}}</div>
          <div class="value">${{escapeHtml(value)}}</div>
        </div>
      `).join("");
    }}

    function renderToolbar(data) {{
      const inputs = data.inputs || {{}};
      const chips = [
        `抓取方式 后台服务持续轮询`,
        `模式 ${inputs.showtype || ""}`,
        `球种 ${((inputs.gtypes || []).map(sportLabel)).join(" / ")}`,
        `详情 ${inputs.include_more ? "已开启" : "未开启"}`,
        `快照时间 ${formatSnapshotMeaning(data.snapshot_time)}`,
        `当前查看 ${uiState.selectedSport === "ALL" ? "全部球种" : sportLabel(uiState.selectedSport)}`
      ];
      document.getElementById("toolbar-meta").innerHTML = chips.map(text => `<div class="tool">${escapeHtml(text)}</div>`).join("");
      document.getElementById("toggle-raw-button").textContent = uiState.showRaw ? "隐藏原始 JSON" : "显示原始 JSON";
    }}

    function renderSummary(data) {{
      const feeds = data.feeds || {{}};
      const cards = [
        `<article class="summary-card ${uiState.selectedSport === "ALL" ? "active" : ""}" data-sport="ALL">
          <div class="name">全部</div>
          <div class="count">${Object.values(feeds).reduce((sum, feed) => sum + (((feed.parsed || {{}}).games || []).length), 0)}</div>
          <div class="meta">点击查看全部球种</div>
        </article>`
      ];
      const html = Object.entries(feeds).map(([gtype, feed]) => {{
        const games = (feed.parsed && feed.parsed.games) || [];
        const leagues = new Set(games.map(game => game.league || (game.fields && game.fields.LEAGUE) || "").filter(Boolean));
        const moreCount = feed.game_more ? Object.keys(feed.game_more).length : 0;
        return `
          <article class="summary-card ${uiState.selectedSport === gtype ? "active" : ""}" data-sport="${escapeHtml(gtype)}">
            <div class="name">${{escapeHtml(sportLabel(gtype))}}</div>
            <div class="count">${{games.length}}</div>
            <div class="meta">${{leagues.size}} 个联赛${{moreCount ? ` · ${moreCount} 场全盘口` : ""}}</div>
          </article>
        `;
      }}).join("");
      document.getElementById("summary").innerHTML = (cards.join("") + html) || `<article class="summary-card"><div class="name">状态</div><div class="count">0</div><div class="meta">暂无数据</div></article>`;
      document.querySelectorAll(".summary-card[data-sport]").forEach(card => {{
        card.onclick = () => {{
          uiState.selectedSport = card.dataset.sport || "ALL";
          rerender();
        }};
      }});
    }}

    function matchesQuery(game, query) {{
      if (!query) return true;
      const haystack = [
        game.league || "",
        game.team_h || "",
        game.team_c || "",
        game.ecid || "",
        (game.fields && game.fields.LEAGUE) || "",
        (game.fields && game.fields.TEAM_H) || "",
        (game.fields && game.fields.TEAM_C) || ""
      ].join(" ").toLowerCase();
      return haystack.includes(query);
    }}

    function renderSports(data) {{
      const feeds = data.feeds || {{}};
      const selectedEntries = Object.entries(feeds).filter(([gtype]) => uiState.selectedSport === "ALL" || uiState.selectedSport === gtype);
      const html = selectedEntries.map(([gtype, feed]) => {{
        const allGames = (feed.parsed && feed.parsed.games) || [];
        const games = allGames.filter(game => matchesQuery(game, uiState.query));
        if (!games.length) {{
          return `
            <section class="sport">
              <div class="sport-head">
                <h2>${{escapeHtml(sportLabel(gtype))}}</h2>
                <div class="meta">${{allGames.length}} 场，筛选后 0 场</div>
              </div>
              <div class="panel empty">当前筛选条件下没有比赛</div>
            </section>
          `;
        }}
        const leagueCounts = new Map();
        for (const game of games) {{
          const league = game.league || (game.fields && game.fields.LEAGUE) || "未命名联赛";
          leagueCounts.set(league, (leagueCounts.get(league) || 0) + 1);
        }}
        const leagueHtml = [...leagueCounts.entries()]
          .sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0]))
          .slice(0, 18)
          .map(([league, count]) => `<div class="league-row"><span>${{escapeHtml(league)}}</span><strong>${{count}}</strong></div>`)
          .join("");

        const matchHtml = games.slice(0, 120).map(game => {{
          const fields = game.fields || {{}};
          const moreMap = feed.game_more || {{}};
          const ecid = game.ecid || fields.ECID || "";
          const moreFields = moreMap[ecid] && moreMap[ecid].parsed && moreMap[ecid].parsed.game && moreMap[ecid].parsed.game.fields;
          const odds = buildOddsLine(fields);
          const halfOdds = buildHalfOddsLine(fields);
          const detail = buildMoreLine(moreFields);
          const detailInfo = currentDetail(game);
          const categoryCounts = currentCategoryCounts(game);
          const categoryTotal = detailInfo && detailInfo.market_count
            ? detailInfo.market_count
            : Object.values(categoryCounts).reduce((sum, value) => sum + value, 0);
          const categoryChips = renderCategoryChips(game);
          const categoryPreview = renderCategoryPreview(game);
          const scoreH = game.score_h || "";
          const scoreC = game.score_c || "";
          return `
            <article class="match-card">
              <div class="match-top">
                <div>
                  <div class="league">${{escapeHtml(game.league || "")}}</div>
                  <div class="teams">${{escapeHtml(game.team_h || "")}} vs ${{escapeHtml(game.team_c || "")}}</div>
                </div>
                <div class="score">${{escapeHtml(`${{scoreH}} : ${{scoreC}}`)}}</div>
              </div>
              <div class="meta-row">
                <span>${{escapeHtml(game.retimeset || "")}}</span>
                <span>ECID ${{escapeHtml(ecid)}}</span>
                <span>更多 ${{escapeHtml(game.more || "")}}</span>
                <span>${{escapeHtml(fields.GOPEN || "")}} / ${{escapeHtml(fields.HGOPEN || "")}}</span>
                <span>分类 ${{escapeHtml(Object.keys(categoryCounts).length)}} 类</span>
                <span>盘口 ${{escapeHtml(categoryTotal)}} 项</span>
              </div>
              <div class="odds-row">${{odds.map(item => `<span>${{escapeHtml(item)}}</span>`).join("") || '<span>当前无主盘口值</span>'}}</div>
              ${{halfOdds.length ? `<div class="odds-row" style="color:var(--accent)">${{halfOdds.map(item => `<span>${{escapeHtml(item)}}</span>`).join("")}}</div>` : ""}}
              ${{detail ? `<div class="detail-row"><span>${{escapeHtml(detail)}}</span></div>` : ""}}
              ${{categoryChips ? `<div class="category-row">${{categoryChips}}</div>` : ""}}
              ${{categoryPreview ? `<details class="market-detail"><summary>查看分类盘口摘要</summary><div class="category-preview">${{categoryPreview}}</div></details>` : ""}}
            </article>
          `;
        }}).join("");

        return `
          <section class="sport">
            <div class="sport-head">
              <h2>${{escapeHtml(sportLabel(gtype))}}</h2>
              <div class="meta">${{games.length}} / ${{allGames.length}} 场实时比赛</div>
            </div>
            <div class="sport-grid">
              <div class="panel">
                <h3>联赛分布</h3>
                ${{leagueHtml || '<div class="empty">暂无联赛分布</div>'}}
              </div>
              <div class="panel">
                <h3>比赛列表</h3>
                <div class="matches">${{matchHtml}}</div>
              </div>
            </div>
          </section>
        `;
      }}).join("");
      document.getElementById("sports").innerHTML = html || `<section class="panel empty">没有可展示的球种</section>`;
    }}

    function rerender() {{
      if (!latestData || !latestStatus) return;
      renderStatus(latestStatus, latestData);
      renderToolbar(latestData);
      renderSummary(latestData);
      renderSports(latestData);
      document.getElementById("raw-panel").classList.toggle("hidden", !uiState.showRaw);
      document.getElementById("raw-json").textContent = JSON.stringify(latestData, null, 2);
    }}

    async function loadStatus() {{
      const response = await fetch(`/api/status.json?ts=${{Date.now()}}`, {{ cache: "no-store" }});
      if (!response.ok) throw new Error(`status ${{response.status}}`);
      return await response.json();
    }}

    async function loadData() {{
      const response = await fetch(`/api/latest.json?ts=${{Date.now()}}`, {{ cache: "no-store" }});
      if (!response.ok) throw new Error(`latest ${{response.status}}`);
      return await response.json();
    }}

    let prevDataJson = "";

    async function tick() {{
      try {{
        const [status, data] = await Promise.all([loadStatus(), loadData()]);
        latestStatus = status;
        latestData = data;
        const dataJson = JSON.stringify(data);
        if (dataJson !== prevDataJson) {{
          prevDataJson = dataJson;
          rerender();
        }}
      }} catch (error) {{
        document.getElementById("sports").innerHTML = `<section class="panel error">页面仍在等待后台数据: ${{escapeHtml(error.message)}}</section>`;
      }}
    }}

    document.getElementById("search-input").addEventListener("input", (event) => {{
      uiState.query = (event.target.value || "").trim().toLowerCase();
      rerender();
    }});
    document.getElementById("refresh-button").addEventListener("click", () => {{
      tick();
    }});
    document.getElementById("reset-button").addEventListener("click", () => {{
      uiState.selectedSport = "ALL";
      uiState.query = "";
      document.getElementById("search-input").value = "";
      rerender();
    }});
    document.getElementById("toggle-raw-button").addEventListener("click", () => {{
      uiState.showRaw = !uiState.showRaw;
      rerender();
    }});

    tick();
    setInterval(tick, refreshMs);
  </script>
</body>
</html>
"""


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def safe_print(*args, **kwargs) -> None:
    try:
        print(*args, **kwargs)
    except BrokenPipeError:
        return
    except OSError as exc:
        if exc.errno == errno.EPIPE:
            return
        raise


def load_live_inputs(args: argparse.Namespace) -> tuple[str, str | None, dict[str, str], list[str], str, str]:
    body = poller.read_text(
        inline_value=args.body,
        file_path=args.body_file,
        env_name="GET_GAME_LIST_BODY",
    )
    cookie = poller.read_text(
        inline_value=args.cookie,
        file_path=args.cookie_file,
        env_name="GET_GAME_LIST_COOKIE",
    )

    # Auto-login mode: use LOGIN_USERNAME/PASSWORD if cookie/body missing
    if not body or not cookie:
        login_user = os.environ.get("LOGIN_USERNAME", "")
        login_pass = os.environ.get("LOGIN_PASSWORD", "")
        entry_url = os.environ.get("ENTRY_URL", "https://112.121.42.168")
        if login_user and login_pass:
            safe_print(f"Auto-login as {login_user}...", file=sys.stderr)
            creds = auto_login.auto_login(login_user, login_pass, entry_url)
            cookie = creds["cookie"]
            body = creds["body_template"]
            safe_print(f"Auto-login OK: uid={creds['uid']}", file=sys.stderr)
        elif not body:
            raise SystemExit(
                "Missing request body. Use --body, --body-file, GET_GAME_LIST_BODY, "
                "or set LOGIN_USERNAME/LOGIN_PASSWORD for auto-login."
            )

    gtypes = poller.normalize_gtypes(args.gtypes)
    showtype = args.showtype
    rtype = args.rtype
    if rtype == "auto":
        rtype = poller.SHOWTYPE_TO_RTYPE.get(showtype, "r")
    template = poller.parse_form_body(body)
    return body, cookie, template, gtypes, showtype, rtype


def build_app_html(title: str, refresh_ms: int) -> str:
    return (
        APP_HTML.replace("__TITLE__", title)
        .replace("__REFRESH_MS__", str(refresh_ms))
        .replace("__REFRESH_SECONDS__", f"{refresh_ms / 1000:.1f}")
        .replace("{{", "{")
        .replace("}}", "}")
    )


def make_handler(
    *,
    outdir: Path,
    status_path: Path,
    app_html: str,
    db_conn: db_store.ThreadSafeDB | None = None,
    db_path: str = "",
) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, format: str, *args: Any) -> None:
            return

        def send_bytes(self, content: bytes, content_type: str, status: int = 200) -> None:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Cache-Control", "no-store, no-cache, must-revalidate")
            self.send_header("Pragma", "no-cache")
            self.send_header("Expires", "0")
            self.send_header("Content-Length", str(len(content)))
            self.end_headers()
            self.wfile.write(content)

        def serve_file(self, path: Path, content_type: str) -> None:
            if not path.exists():
                self.send_bytes(b'{"error":"not found"}', "application/json; charset=utf-8", HTTPStatus.NOT_FOUND)
                return
            self.send_bytes(path.read_bytes(), content_type)

        def do_GET(self) -> None:
            parsed = urlparse(self.path)
            route = parsed.path
            if route in ("/", "/index.html"):
                self.send_bytes(app_html.encode("utf-8"), "text/html; charset=utf-8")
                return
            if route == "/api/latest.json":
                self.serve_file(outdir / "latest.json", "application/json; charset=utf-8")
                return
            if route == "/api/status.json":
                self.serve_file(status_path, "application/json; charset=utf-8")
                return
            if route.startswith("/snapshots/"):
                rel = route.removeprefix("/snapshots/")
                target = (outdir / rel).resolve()
                if outdir.resolve() not in target.parents and target != outdir.resolve():
                    self.send_bytes(b'{"error":"forbidden"}', "application/json; charset=utf-8", HTTPStatus.FORBIDDEN)
                    return
                content_type = "application/json; charset=utf-8" if target.suffix == ".json" else "text/plain; charset=utf-8"
                self.serve_file(target, content_type)
                return
            if route == "/api/history":
                self._handle_history(parsed, db_conn)
                return
            if route == "/api/stats":
                self._handle_stats(db_conn, db_path)
                return
            self.send_bytes(b"not found", "text/plain; charset=utf-8", HTTPStatus.NOT_FOUND)

        def _handle_history(self, parsed, db_conn):
            if not db_conn:
                self.send_bytes(json.dumps({"error": "db not enabled"}).encode(), "application/json; charset=utf-8", 400)
                return
            params = urllib.parse.parse_qs(parsed.query)
            gid = params.get("gid", [""])[0]
            ecid = params.get("ecid", [""])[0]
            limit = int(params.get("limit", ["100"])[0])
            since = params.get("since", [""])[0]
            try:
                if gid or ecid:
                    rows = db_store.query_game_history(db_conn, gid=gid, ecid=ecid, limit=limit)
                    self.send_bytes(json.dumps(rows, ensure_ascii=False, default=str).encode(), "application/json; charset=utf-8")
                elif since:
                    rows = db_store.query_snapshots(db_conn, since=since, limit=limit)
                    self.send_bytes(json.dumps(rows, ensure_ascii=False, default=str).encode(), "application/json; charset=utf-8")
                else:
                    rows = db_store.query_latest_games(db_conn, limit=limit)
                    self.send_bytes(json.dumps(rows, ensure_ascii=False, default=str).encode(), "application/json; charset=utf-8")
            except Exception as exc:
                self.send_bytes(json.dumps({"error": str(exc)}).encode(), "application/json; charset=utf-8", 500)

        def _handle_stats(self, db_conn, db_path):
            if not db_conn:
                self.send_bytes(json.dumps({"error": "db not enabled"}).encode(), "application/json; charset=utf-8", 400)
                return
            try:
                stats = db_store.get_db_stats(db_conn, db_path=db_path)
                self.send_bytes(json.dumps(stats, ensure_ascii=False).encode(), "application/json; charset=utf-8")
            except Exception as exc:
                self.send_bytes(json.dumps({"error": str(exc)}).encode(), "application/json; charset=utf-8", 500)

    return Handler


def polling_loop(
    *,
    args: argparse.Namespace,
    outdir: Path,
    status_path: Path,
    cookie: str | None,
    template: dict[str, str],
    gtypes: list[str],
    showtype: str,
    rtype: str,
    stop_event: threading.Event,
    server_url: str,
    db_conn: db_store.ThreadSafeDB | None = None,
    login_user: str = "",
    login_pass: str = "",
    entry_url: str = "",
) -> None:
    consecutive_failures = 0
    max_failures_before_relogin = 5
    cleanup_counter = 0

    def payload_has_double_login(payload: dict[str, Any]) -> bool:
        for feed in payload.get("feeds", {}).values():
            raw = feed.get("raw_response", "")
            if "doubleLogin" in raw:
                return True
        return False

    while not stop_event.is_set():
        started = time.time()
        status: dict[str, Any] = {
            "running": True,
            "output_dir": str(outdir),
            "url": server_url,
            "last_attempt": datetime.now().isoformat(),
            "last_success": "",
            "last_error": "",
        }
        try:
            payload, total_games, total_more, errors = poller.collect_live_payload(
                args,
                cookie=cookie,
                template=template,
                gtypes=gtypes,
                showtype=showtype,
                rtype=rtype,
            )
            need_relogin = payload_has_double_login(payload)

            if need_relogin and login_user and login_pass:
                safe_print("[polling] doubleLogin detected, re-logging in...", file=sys.stderr)
                try:
                    creds = auto_login.auto_login(login_user, login_pass, entry_url)
                    cookie = creds["cookie"]
                    template = poller.parse_form_body(creds["body_template"])
                    consecutive_failures = 0
                    status["last_error"] = ""
                    status["auto_relogin"] = f"OK uid={creds['uid']}"
                    safe_print(f"[polling] re-login OK: uid={creds['uid']}", file=sys.stderr)
                    payload, total_games, total_more, errors = poller.collect_live_payload(
                        args,
                        cookie=cookie,
                        template=template,
                        gtypes=gtypes,
                        showtype=showtype,
                        rtype=rtype,
                    )
                    need_relogin = payload_has_double_login(payload)
                    if need_relogin:
                        status["last_error"] = "doubleLogin persisted after auto re-login"
                        safe_print("[polling] doubleLogin still present after re-login", file=sys.stderr)
                except Exception as login_err:
                    status["last_error"] = f"re-login failed: {login_err}"
                    safe_print(f"[polling] re-login FAILED: {login_err}", file=sys.stderr)

            if not need_relogin:
                # Only write latest.json (no timestamped archives)
                poller.write_latest(outdir, payload)
                # Write to SQLite if enabled
                if db_conn is not None:
                    try:
                        db_store.insert_snapshot(db_conn, payload)
                    except Exception as db_err:
                        safe_print(f"[db] write error: {db_err}", file=sys.stderr)
                summary = poller.summarize_payload(
                    payload,
                    snapshot=outdir / "latest.json",
                    total_games=total_games,
                    total_more=total_more,
                    errors=errors,
                )
                status["last_success"] = payload.get("snapshot_time", "")
                status["last_summary"] = summary
                if errors:
                    status["last_error"] = json.dumps(errors, ensure_ascii=False)
                consecutive_failures = 0
        except Exception as exc:
            consecutive_failures += 1
            status["last_error"] = str(exc)

            # Auto re-login on repeated failures
            if (consecutive_failures >= max_failures_before_relogin
                    and login_user and login_pass):
                safe_print(f"[polling] {consecutive_failures} consecutive failures, re-logging in...", file=sys.stderr)
                try:
                    creds = auto_login.auto_login(login_user, login_pass, entry_url)
                    cookie = creds["cookie"]
                    template = poller.parse_form_body(creds["body_template"])
                    consecutive_failures = 0
                    status["last_error"] = ""
                    status["auto_relogin"] = f"OK uid={creds['uid']}"
                    safe_print(f"[polling] re-login OK: uid={creds['uid']}", file=sys.stderr)
                except Exception as login_err:
                    status["last_error"] = f"re-login failed: {login_err}"
                    safe_print(f"[polling] re-login FAILED: {login_err}", file=sys.stderr)

        write_json(status_path, status)

        # Periodic cleanup: delete old snapshot files every 60 rounds
        cleanup_counter += 1
        if cleanup_counter >= 60:
            cleanup_counter = 0
            deleted = db_store.cleanup_old_snapshot_files(outdir, keep_minutes=5)
            if deleted:
                safe_print(f"[cleanup] deleted {deleted} old snapshot files", file=sys.stderr)

        elapsed = time.time() - started
        wait_seconds = max(0.0, args.interval - elapsed)
        stop_event.wait(wait_seconds)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run a local live dashboard backed by the poller.")
    parser.add_argument("--url", default=poller.DEFAULT_URL, help="Feed URL")
    parser.add_argument("--body", help="Inline POST body")
    parser.add_argument("--body-file", help="Path to file containing POST body")
    parser.add_argument("--cookie", help="Inline Cookie header")
    parser.add_argument("--cookie-file", help="Path to file containing Cookie header")
    parser.add_argument("--gtypes", default="ft,bk,es,tn,vb,bm,tt,bs,sk,op", help="Comma-separated sport codes")
    parser.add_argument("--showtype", default="live", choices=("live", "today", "early"), help="Match list type")
    parser.add_argument("--rtype", default="auto", help="Request rtype. Use auto for default mapping")
    parser.add_argument("--include-more", action="store_true", help="Also fetch get_game_more")
    parser.add_argument("--more-filter", default="Main", help="Filter for get_game_more")
    parser.add_argument("--more-delay", type=float, default=0.0, help="Delay between get_game_more requests")
    parser.add_argument("--interval", type=float, default=5.0, help="Polling interval in seconds")
    parser.add_argument("--timeout", type=float, default=15.0, help="Request timeout in seconds")
    parser.add_argument("--output-dir", default="live_service_data", help="Directory to store snapshots")
    parser.add_argument("--title", default="全部比赛实时看板", help="Page title")
    parser.add_argument("--host", default="127.0.0.1", help="Host to bind")
    parser.add_argument("--port", type=int, default=8765, help="Port to bind")
    parser.add_argument("--refresh-ms", type=int, default=3000, help="Frontend refresh interval in milliseconds")
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    _, cookie, template, gtypes, showtype, rtype = load_live_inputs(args)

    outdir = poller.ensure_output_dir(args.output_dir)
    status_path = outdir / "status.json"

    # Initialize SQLite database
    db_conn = None
    db_path = outdir / "history.db"
    if os.environ.get("DB_ENABLED", "1") == "1":
        db_conn = db_store.init_db(str(db_path))
        # Startup cleanup
        keep_hours = int(os.environ.get("DB_KEEP_HOURS", "24"))
        deleted_files = db_store.cleanup_old_snapshot_files(outdir, keep_minutes=0)
        if deleted_files:
            safe_print(f"[startup] cleaned {deleted_files} old snapshot files", file=sys.stderr)
        stats = db_store.get_db_stats(db_conn)
        safe_print(f"[db] history.db ready: {stats['snapshot_count']} snapshots, {stats['db_size_mb']}MB", file=sys.stderr)
    if not status_path.exists():
        write_json(
            status_path,
            {
                "running": True,
                "output_dir": str(outdir),
                "url": f"http://{args.host}:{args.port}",
                "last_attempt": "",
                "last_success": "",
                "last_error": "等待首次抓取",
            },
        )

    app_html = build_app_html(args.title, args.refresh_ms)
    handler = make_handler(outdir=outdir, status_path=status_path, app_html=app_html, db_conn=db_conn, db_path=str(db_path))
    server = ThreadingHTTPServer((args.host, args.port), handler)
    stop_event = threading.Event()
    server_url = f"http://{args.host}:{args.port}"
    worker = threading.Thread(
        target=polling_loop,
        kwargs={
            "args": args,
            "outdir": outdir,
            "status_path": status_path,
            "cookie": cookie,
            "template": template,
            "gtypes": gtypes,
            "showtype": showtype,
            "rtype": rtype,
            "stop_event": stop_event,
            "server_url": server_url,
            "db_conn": db_conn,
            "login_user": os.environ.get("LOGIN_USERNAME", ""),
            "login_pass": os.environ.get("LOGIN_PASSWORD", ""),
            "entry_url": os.environ.get("ENTRY_URL", "https://112.121.42.168"),
        },
        daemon=True,
    )
    worker.start()
    safe_print(json.dumps({"url": server_url, "output_dir": str(outdir)}, ensure_ascii=False))
    try:
        server.serve_forever(poll_interval=0.5)
    except KeyboardInterrupt:
        pass
    finally:
        stop_event.set()
        server.shutdown()
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
