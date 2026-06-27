#!/usr/bin/env python3
"""viva — section-by-section markdown review server.

Usage:
  python server.py --mode review --input .viva/review-input-r1.json --output .viva/review-r1.json
  python server.py --mode qa     --input .viva/qa-input.json        --output .viva/answers.json
"""
import argparse
import base64
import json
import os
import re
import signal
import socket
import sys
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from socketserver import ThreadingMixIn
from urllib.parse import parse_qs, urlparse

HTML = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>viva</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://cdn.jsdelivr.net" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Bricolage+Grotesque:opsz,wght@12..96,300;12..96,400;12..96,500;12..96,600&family=Fragment+Mono:ital@0;1&display=swap" rel="stylesheet">
<script defer src="https://cdn.jsdelivr.net/npm/marked@12/marked.min.js"></script>
<script defer src="https://cdn.jsdelivr.net/npm/dompurify@3/dist/purify.min.js"></script>
<style>
/* ─── Tokens ─────────────────────────────────────────────── */
/* Blueprint: drafting-table blue, cyan linework, red-pencil markup.
   --teal/--orange/--violet are the verdict slots: approve / red-pencil / rfi */
:root {
  --bg:        #0a1727;
  --bg2:       #0f1f33;
  --bg3:       #152840;
  --border:    #1d324e;
  --border2:   #2a4768;
  --text:      #d8e7f5;
  --text2:     #7f9cba;
  --text3:     #48648a;
  --accent:    #5cc8ff;
  --accent-dim:rgba(92,200,255,0.08);
  --teal:      #43e0a8;
  --teal-bg:   rgba(67,224,168,0.06);
  --orange:    #ff5a36;
  --orange-bg: rgba(255,90,54,0.08);
  --violet:    #ffc857;
  --violet-bg: rgba(255,200,87,0.08);
}

/* ─── Light mode ─────────────────────────────────────────── */
/* Light mode: blueline print — blue ink on white vellum */
@media (prefers-color-scheme: light) {
  :root {
    --bg:        #f3f6fa;
    --bg2:       #e9eef5;
    --bg3:       #dde5ef;
    --border:    #cdd9e8;
    --border2:   #a8bdd4;
    --text:      #13293f;
    --text2:     #446080;
    --text3:     #8aa0b8;
    --accent:    #1271b8;
    --accent-dim:rgba(18,113,184,0.08);
    --teal:      #0c8a63;
    --teal-bg:   rgba(12,138,99,0.07);
    --orange:    #cf3f1d;
    --orange-bg: rgba(207,63,29,0.07);
    --violet:    #9a6b00;
    --violet-bg: rgba(154,107,0,0.08);
  }

  body {
    background-image:
      linear-gradient(rgba(168,189,212,0.30) 1px, transparent 1px),
      linear-gradient(90deg, rgba(168,189,212,0.30) 1px, transparent 1px);
    background-size: 24px 24px;
  }

  .progress-fill {
    box-shadow: 0 0 6px rgba(18,113,184,0.35), 0 0 2px rgba(18,113,184,0.6);
  }

  .dot-approved { box-shadow: 0 0 5px rgba(12,138,99,0.4); }
  .dot-active   { box-shadow: 0 0 5px rgba(207,63,29,0.4); }
  .dot-changes  { box-shadow: none; }
  .dot-info     { box-shadow: none; }

  .titleblock { background: #fff; }
  .ledger { background: #fff; }

  .section-content { background: #fff; }
  .section-content::-webkit-scrollbar-thumb { border-color: #fff; }
  .section-content pre { background: var(--bg); }
  .section-content code { background: var(--bg2); }

  .note-field { background: var(--bg); }

  .bottom-bar {
    background: rgba(243,246,250,0.92);
    border-top-color: var(--border);
  }

  .btn-submit.ready {
    background: var(--accent);
    color: #fff;
    box-shadow: 0 0 16px rgba(18,113,184,0.2);
  }
  .btn-submit.ready:hover {
    box-shadow: 0 0 24px rgba(18,113,184,0.3);
  }

  .card { background: #fff; }
  .card-body { background: var(--bg); }
  .card-head:hover { background: var(--bg2); }
  .card.is-active { box-shadow: 0 0 0 1px var(--border2), 0 4px 20px rgba(0,0,0,0.08); }
}

/* ─── Reset ──────────────────────────────────────────────── */
*,*::before,*::after { box-sizing:border-box; margin:0; padding:0; }
button { font-family:inherit; cursor:pointer; }
textarea { font-family:inherit; }

/* ─── Base ───────────────────────────────────────────────── */
html { scroll-behavior: smooth; }

body {
  font-family: 'Bricolage Grotesque', sans-serif;
  background: var(--bg);
  color: var(--text);
  min-height: 100vh;
  -webkit-font-smoothing: antialiased;
  /* drafting grid paper */
  background-image:
    linear-gradient(rgba(42,71,104,0.28) 1px, transparent 1px),
    linear-gradient(90deg, rgba(42,71,104,0.28) 1px, transparent 1px);
  background-size: 24px 24px;
}

/* ─── Shell ──────────────────────────────────────────────── */
.shell {
  max-width: 700px;
  margin: 0 auto;
  padding: 40px 20px 140px;
}

/* ─── Header ─────────────────────────────────────────────── */
.header {
  margin-bottom: 36px;
  animation: fadeUp 0.4s ease both;
}

/* ─── Title block — the drafting-sheet header ───────────── */
.titleblock {
  display: flex;
  border: 1px solid var(--border2);
  background: var(--bg2);
  margin-bottom: 10px;
}
.tb-cell { padding: 10px 14px; border-right: 1px solid var(--border2); min-width: 0; }
.tb-cell:last-child { border-right: none; }
.tb-flex { flex: 1; }
.tb-label {
  font-family: 'Fragment Mono', monospace;
  font-size: 8px;
  letter-spacing: 0.16em;
  text-transform: uppercase;
  color: var(--text3);
  margin-bottom: 4px;
  white-space: nowrap;
}
.tb-val {
  font-size: 14px;
  font-weight: 600;
  color: var(--text);
  line-height: 1.3;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.tb-val em { font-style: italic; color: var(--text2); }
.tb-val.mono {
  font-family: 'Fragment Mono', monospace;
  font-size: 12px;
  color: var(--accent);
  padding-top: 2px;
}

/* ─── Revision ledger ────────────────────────────────────── */
.ledger {
  border: 1px solid var(--border2);
  background: var(--bg2);
  margin-bottom: 14px;
  animation: fadeUp 0.4s ease both;
}
.ledger-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 8px 14px;
  cursor: pointer;
  user-select: none;
}
.ledger-head:hover { background: var(--bg3); }
.ledger-title {
  font-family: 'Fragment Mono', monospace;
  font-size: 9px;
  font-weight: 600;
  letter-spacing: 0.16em;
  text-transform: uppercase;
  color: var(--text2);
}
.ledger-chevron { font-size: 10px; color: var(--text3); transition: transform 0.2s; }
.ledger.is-collapsed .ledger-chevron { transform: rotate(-90deg); }
.ledger-body-wrap {
  display: grid;
  grid-template-rows: 1fr;
  transition: grid-template-rows 0.28s cubic-bezier(0.4,0,0.2,1);
}
.ledger.is-collapsed .ledger-body-wrap { grid-template-rows: 0fr; }
.ledger-body-inner { overflow: hidden; }
.ledger-rows { padding: 0 14px 10px; }
.ledger-row {
  display: flex;
  gap: 10px;
  align-items: baseline;
  padding: 5px 0;
  border-top: 1px solid var(--border);
  font-size: 12px;
}
.ledger-round {
  font-family: 'Fragment Mono', monospace;
  font-size: 10px;
  color: var(--text3);
  flex-shrink: 0;
}
.ledger-section { color: var(--text); font-weight: 500; flex-shrink: 0; }
.ledger-verdict {
  font-family: 'Fragment Mono', monospace;
  font-size: 9px;
  letter-spacing: 0.08em;
  text-transform: uppercase;
  flex-shrink: 0;
}
.ledger-verdict.v-changes { color: var(--orange); }
.ledger-verdict.v-info    { color: var(--violet); }
.ledger-note { color: var(--text2); font-style: italic; min-width: 0; }
.ledger.ledger-static .ledger-head { cursor: default; }
.ledger.ledger-static .ledger-head:hover { background: none; }
.complete-inner .ledger { width: 100%; max-width: 560px; text-align: left; margin-top: 1.5rem; }

.progress-track {
  flex: 1;
  height: 2px;
  background: var(--border2);
  border-radius: 2px;
  overflow: visible;
  position: relative;
}

.progress-fill {
  height: 100%;
  background: var(--accent);
  border-radius: 2px;
  transition: width 0.6s cubic-bezier(0.4,0,0.2,1);
  box-shadow: 0 0 10px rgba(92,200,255,0.5), 0 0 2px rgba(92,200,255,0.8);
}

.progress-label {
  font-family: 'Fragment Mono', monospace;
  font-size: 10px;
  color: var(--text3);
  white-space: nowrap;
  letter-spacing: 0.06em;
}

/* ─── Cards ──────────────────────────────────────────────── */
.cards { display: flex; flex-direction: column; gap: 6px; }

.card {
  position: relative;
  border: 1px solid var(--border);
  background: var(--bg2);
  transition: border-color 0.2s, opacity 0.35s, box-shadow 0.2s;
  animation: fadeUp 0.4s ease both;
}

/* registration marks pin the active sheet to the table */
.card::before, .card::after {
  content: '+';
  position: absolute;
  font-family: 'Fragment Mono', monospace;
  font-size: 12px;
  line-height: 1;
  color: var(--accent);
  opacity: 0;
  transition: opacity 0.2s;
  pointer-events: none;
}
.card::before { top: -7px; left: -6px; }
.card::after  { bottom: -7px; right: -6px; }
.card.is-active::before, .card.is-active::after { opacity: 1; }

.card:nth-child(1)  { animation-delay: 0.05s; }
.card:nth-child(2)  { animation-delay: 0.09s; }
.card:nth-child(3)  { animation-delay: 0.13s; }
.card:nth-child(4)  { animation-delay: 0.17s; }
.card:nth-child(5)  { animation-delay: 0.21s; }
.card:nth-child(6)  { animation-delay: 0.25s; }
.card:nth-child(7)  { animation-delay: 0.29s; }
.card:nth-child(8)  { animation-delay: 0.33s; }

@keyframes fadeUp {
  from { opacity:0; transform:translateY(8px); }
  to   { opacity:1; transform:translateY(0); }
}

.card.is-approved { opacity: 0.42; }
.card.is-approved:hover { opacity: 0.72; transition: opacity 0.2s; }

.card.is-active {
  border-color: var(--border2);
  box-shadow: 0 0 0 1px var(--border2), 0 4px 24px rgba(0,0,0,0.4);
}

/* ─── Card head ──────────────────────────────────────────── */
.card-head {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 11px 14px;
  cursor: pointer;
  user-select: none;
  transition: background 0.12s;
  min-height: 48px;
}
.card-head:hover { background: var(--bg3); }

/* dot */
.dot {
  width: 7px; height: 7px;
  border-radius: 50%;
  flex-shrink: 0;
  transition: background 0.25s, box-shadow 0.25s;
}
.dot-idle     { background: var(--text3); }
.dot-active   { background: var(--orange); box-shadow: 0 0 7px rgba(255,140,66,0.6); }
.dot-approved { background: var(--teal);   box-shadow: 0 0 7px rgba(77,255,195,0.5); }
.dot-changes  { background: var(--orange); box-shadow: 0 0 5px rgba(255,140,66,0.4); }
.dot-info     { background: var(--violet); box-shadow: 0 0 5px rgba(167,139,250,0.4); }

.card-title-wrap { flex: 1; min-width: 0; }

.card-title {
  font-size: 13px;
  font-weight: 500;
  color: var(--text);
  line-height: 1.4;
}

.note-inline {
  font-size: 11px;
  color: var(--text3);
  font-style: italic;
  margin-top: 2px;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

/* verdict badge */
.vbadge {
  font-family: 'Fragment Mono', monospace;
  font-size: 9px;
  font-weight: 600;
  letter-spacing: 0.08em;
  text-transform: uppercase;
  padding: 3px 8px;
  border-radius: 3px;
  flex-shrink: 0;
}
.vbadge-approved { background: var(--teal-bg);   color: var(--teal);   }
.vbadge-changes  { background: var(--orange-bg);  color: var(--orange); }
.vbadge-info     { background: var(--violet-bg);  color: var(--violet); }

/* annotation strip — advisory pre-review flags. Reuses the verdict color
   slots: info → teal, warn → violet (amber #ffc857), error → orange.
   Advisory only — they decorate a card, they never gate a verdict. */
.annot-strip { display: flex; flex-direction: column; gap: 5px; margin-bottom: 12px; }
.annot {
  display: flex;
  align-items: baseline;
  gap: 8px;
  font-size: 11.5px;
  line-height: 1.5;
  padding: 6px 9px;
  border-radius: 5px;
  border-left: 2px solid var(--border2);
}
.annot-kind {
  font-family: 'Fragment Mono', monospace;
  font-size: 9px;
  font-weight: 600;
  letter-spacing: 0.08em;
  text-transform: uppercase;
  flex-shrink: 0;
  white-space: nowrap;
}
.annot-msg { color: var(--text2); min-width: 0; overflow-wrap: break-word; }
.annot-info  { background: var(--teal-bg);   border-color: var(--teal);   }
.annot-warn  { background: var(--violet-bg);  border-color: var(--violet); }
.annot-error { background: var(--orange-bg);  border-color: var(--orange); }
.annot-info  .annot-kind { color: var(--teal);   }
.annot-warn  .annot-kind { color: var(--violet); }
.annot-error .annot-kind { color: var(--orange); }

/* ─── Card body (smooth height animation) ────────────────── */
.card-body-wrap {
  display: grid;
  grid-template-rows: 0fr;
  transition: grid-template-rows 0.28s cubic-bezier(0.4,0,0.2,1);
}
.card.is-active .card-body-wrap {
  grid-template-rows: 1fr;
}
.card-body-inner {
  overflow: hidden;
}

.card-body {
  padding: 14px 16px 16px;
  border-top: 1px solid var(--border);
  background: var(--bg);
}

.section-summary {
  font-size: 13px;
  line-height: 1.65;
  color: var(--text2);
  margin-bottom: 12px;
}

/* The document itself — a quiet page surface inside the card chrome */
.section-content {
  font-family: 'Bricolage Grotesque', sans-serif;
  font-size: 13.5px;
  font-weight: 300;
  line-height: 1.7;
  color: var(--text);
  padding: 16px 18px 14px;
  border: 1px solid var(--border);
  background: var(--bg2);
  border-radius: 6px;
  margin-bottom: 14px;
  overflow-wrap: break-word;
  max-height: 60vh;
  overflow-y: auto;
}
.section-content::-webkit-scrollbar { width: 10px; }
.section-content::-webkit-scrollbar-thumb {
  background: var(--border2);
  border-radius: 5px;
  border: 3px solid var(--bg2);
}
.section-content::-webkit-scrollbar-track { background: transparent; }

/* offline fallback: raw verbatim source */
.section-content.md-raw {
  font-family: 'Fragment Mono', monospace;
  font-size: 12px;
  line-height: 1.75;
  white-space: pre-wrap;
}

.section-content p { margin: 0 0 12px; }
.section-content > *:last-child { margin-bottom: 0; }
.section-content strong { font-weight: 500; color: var(--text); }
.section-content em { color: var(--text2); }

/* In-document headings: title-block lettering for majors, mono overline for minors */
.section-content h1, .section-content h2 {
  font-size: 14px;
  font-weight: 600;
  letter-spacing: 0.04em;
  text-transform: uppercase;
  color: var(--text);
  margin: 18px 0 8px;
}
.section-content h3 {
  font-size: 13px;
  font-weight: 600;
  color: var(--text);
  margin: 16px 0 6px;
}
.section-content h4, .section-content h5, .section-content h6 {
  font-family: 'Fragment Mono', monospace;
  font-size: 10px;
  font-weight: 600;
  letter-spacing: 0.1em;
  text-transform: uppercase;
  color: var(--text2);
  margin: 16px 0 6px;
}
.section-content h1:first-child, .section-content h2:first-child,
.section-content h3:first-child, .section-content h4:first-child { margin-top: 2px; }

/* Code and diagrams: cyan linework on the print */
.section-content pre {
  font-family: 'Fragment Mono', monospace;
  font-size: 11px;
  line-height: 1.7;
  background: var(--bg);
  border: 1px dashed var(--border2);
  padding: 12px 14px;
  overflow-x: auto;
  margin: 0 0 12px;
  color: var(--accent);
}
.section-content code {
  font-family: 'Fragment Mono', monospace;
  font-size: 11px;
  background: var(--bg);
  border: 1px solid var(--border);
  padding: 1px 5px;
  color: var(--accent);
}
.section-content pre code { background: none; border: none; padding: 0; color: inherit; }

/* Editorial tables: hairline rows, mono overline headers, no grid boxes */
.section-content table {
  border-collapse: collapse;
  width: 100%;
  margin: 2px 0 14px;
  font-size: 12px;
}
.section-content th {
  font-family: 'Fragment Mono', monospace;
  font-size: 9px;
  font-weight: 600;
  letter-spacing: 0.08em;
  text-transform: uppercase;
  color: var(--text2);
  text-align: left;
  padding: 6px 12px 6px 0;
  border-bottom: 1px solid var(--border2);
}
.section-content td {
  padding: 6px 12px 6px 0;
  border-bottom: 1px solid var(--border);
  color: var(--text2);
}
.section-content tr:last-child td { border-bottom: none; }

.section-content ul, .section-content ol { margin: 0 0 12px; padding-left: 20px; }
.section-content li { margin: 3px 0; }
.section-content li::marker { color: var(--text3); }

.section-content blockquote {
  border-left: 2px solid var(--accent);
  margin: 0 0 12px;
  padding: 2px 14px;
  color: var(--text2);
  font-style: italic;
}
.section-content a {
  color: var(--accent);
  text-decoration: none;
  border-bottom: 1px solid var(--accent-dim);
}
.section-content a:hover { border-bottom-color: var(--accent); }
.section-content hr { border: none; border-top: 1px solid var(--border); margin: 14px 0; }
.section-content img { max-width: 100%; }

/* ─── Blueprint geometry: drafting sheets have square corners ── */
.card, .action-btn, .note-field, .vbadge, .btn-skip, .btn-submit,
.section-content, .choice-chip, .qa-btn,
.progress-track, .progress-fill { border-radius: 0; }

/* ─── Action buttons ─────────────────────────────────────── */
.actions { display: flex; gap: 6px; flex-wrap: wrap; margin-bottom: 10px; }

.action-btn {
  font-family: 'Fragment Mono', monospace;
  font-size: 10px;
  font-weight: 600;
  letter-spacing: 0.05em;
  padding: 6px 14px;
  border: 1.5px solid var(--border2);
  border-radius: 5px;
  background: transparent;
  color: var(--text2);
  display: flex; align-items: center; gap: 5px;
  transition: border-color 0.12s, color 0.12s, background 0.12s;
}
.action-btn:hover {
  border-color: var(--text3);
  color: var(--text);
}
.action-btn.sel-approve {
  border-color: var(--teal);   color: var(--teal);   background: var(--teal-bg);
}
.action-btn.sel-changes {
  border-color: var(--orange); color: var(--orange); background: var(--orange-bg);
}
.action-btn.sel-info {
  border-color: var(--violet); color: var(--violet); background: var(--violet-bg);
}

/* ─── Note textarea ──────────────────────────────────────── */
.note-field {
  width: 100%;
  font-family: 'Bricolage Grotesque', sans-serif;
  font-size: 13px;
  padding: 9px 12px;
  border: 1px solid var(--border2);
  border-radius: 6px;
  background: var(--bg2);
  color: var(--text);
  resize: vertical;
  min-height: 72px;
  line-height: 1.55;
  transition: border-color 0.15s;
  margin-top: 2px;
  display: block;
}
.note-field:focus { outline: none; border-color: var(--text3); }
.note-field::placeholder { color: var(--text3); }
.thumb-strip {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  margin-top: 6px;
}
.thumb {
  position: relative;
  width: 64px;
  height: 64px;
  border-radius: 6px;
  overflow: hidden;
  border: 1px solid var(--border2);
}
.thumb img { width: 100%; height: 100%; object-fit: cover; display: block; }
.thumb-remove {
  position: absolute;
  top: 1px;
  right: 1px;
  width: 20px;
  height: 20px;
  line-height: 18px;
  text-align: center;
  border: none;
  border-radius: 50%;
  background: rgba(0, 0, 0, 0.6);
  color: #fff;
  cursor: pointer;
  font-size: 12px;
  padding: 0;
}
.attach-btn {
  margin-top: 6px;
  font-family: 'Fragment Mono', monospace;
  font-size: 10px;
  font-weight: 600;
  letter-spacing: 0.05em;
  cursor: pointer;
  background: transparent;
  color: var(--text2);
  border: 1.5px solid var(--border2);
  border-radius: 5px;
  padding: 5px 10px;
  transition: border-color 0.12s, color 0.12s;
}
.attach-btn:hover { border-color: var(--text3); color: var(--text); }
.attach-btn:focus-visible { outline: 2px solid var(--text3); outline-offset: 1px; }
.card.is-drop-target { box-shadow: 0 0 0 2px var(--teal); }

/* ─── Divider between card sections ─────────────────────── */
.sep { height: 1px; background: var(--border); margin: 4px 0; }

/* ─── Q&A choices (chip style) ──────────────────────────── */
.choices-label {
  font-family: 'Fragment Mono', monospace;
  font-size: 9px;
  font-weight: 600;
  letter-spacing: 0.1em;
  text-transform: uppercase;
  color: var(--text3);
  margin-bottom: 7px;
}

.choices { display: flex; flex-wrap: wrap; gap: 6px; margin-bottom: 12px; }

.choice-chip {
  font-size: 12px;
  font-weight: 400;
  padding: 5px 12px;
  border: 1.5px solid var(--border2);
  border-radius: 20px;
  background: transparent;
  color: var(--text2);
  transition: all 0.12s;
  cursor: pointer;
}
.choice-chip:hover { border-color: var(--text3); color: var(--text); }
.choice-chip.selected {
  border-color: var(--accent);
  color: var(--accent);
  background: var(--accent-dim);
}

/* QA action buttons */
.qa-actions { display: flex; gap: 6px; margin-top: 12px; flex-wrap: wrap; }
.qa-btn {
  font-family: 'Fragment Mono', monospace;
  font-size: 10px;
  font-weight: 600;
  letter-spacing: 0.05em;
  padding: 6px 14px;
  border: 1.5px solid var(--border2);
  border-radius: 5px;
  background: transparent;
  color: var(--text2);
  display: flex; align-items: center; gap: 5px;
  transition: all 0.12s;
}
.qa-btn:hover { border-color: var(--text3); color: var(--text); }
.qa-btn.confirm {
  border-color: var(--teal); color: var(--teal); background: var(--teal-bg);
}

/* ─── Bottom bar ─────────────────────────────────────────── */
.bottom-bar {
  position: fixed;
  bottom: 0; left: 0; right: 0;
  z-index: 100;
  padding: 14px 20px;
  background: rgba(10,23,39,0.9);
  backdrop-filter: blur(16px) saturate(180%);
  border-top: 1px solid var(--border);
}

.bottom-inner {
  max-width: 700px;
  margin: 0 auto;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
}

.stats {
  font-family: 'Fragment Mono', monospace;
  font-size: 10px;
  letter-spacing: 0.05em;
  display: flex;
  gap: 14px;
  flex-wrap: wrap;
}
.stat-approved { color: var(--teal); }
.stat-feedback { color: var(--orange); }
.stat-pending  { color: var(--text3); }

.btn-group { display: flex; gap: 8px; }

.btn-skip {
  font-family: 'Fragment Mono', monospace;
  font-size: 10px;
  font-weight: 600;
  letter-spacing: 0.06em;
  padding: 9px 16px;
  border: 1px solid var(--border2);
  border-radius: 6px;
  background: transparent;
  color: var(--text2);
  transition: all 0.15s;
}
.btn-skip:hover { border-color: var(--text3); color: var(--text); }

.btn-submit {
  font-family: 'Fragment Mono', monospace;
  font-size: 10px;
  font-weight: 700;
  letter-spacing: 0.08em;
  text-transform: uppercase;
  padding: 9px 20px;
  border: none;
  border-radius: 6px;
  transition: all 0.2s;
}
.btn-submit.ready {
  background: var(--accent);
  color: var(--bg);
  box-shadow: 0 0 20px rgba(92,200,255,0.25);
}
.btn-submit.ready:hover {
  box-shadow: 0 0 32px rgba(92,200,255,0.4);
  transform: translateY(-1px);
}
.btn-submit.disabled {
  background: var(--border2);
  color: var(--text3);
  cursor: not-allowed;
}
/* ─── Processing / Complete states ──────────────────────── */
@keyframes viva-spin { to { transform: rotate(360deg); } }

.processing-inner {
  display: flex;
  flex-direction: column;
  align-items: center;
  padding: 8rem 2rem;
  color: var(--text2);
}
.spinner {
  width: 44px; height: 44px;
  border: 3px solid var(--border2);
  border-top-color: var(--accent);
  border-radius: 50%;
  animation: viva-spin 0.75s linear infinite;
  margin-bottom: 1.5rem;
}
.processing-text {
  font-family: 'Bricolage Grotesque', sans-serif;
  font-size: 1rem;
  letter-spacing: 0.02em;
}

.complete-inner {
  display: flex;
  flex-direction: column;
  align-items: center;
  padding: 8rem 2rem;
  text-align: center;
}
.complete-check {
  font-size: 2.5rem;
  color: var(--teal);
  margin-bottom: 1.25rem;
}
.complete-headline {
  font-size: 1.6rem;
  font-weight: 600;
  color: var(--text);
  margin-bottom: 0.5rem;
}
.complete-detail {
  font-family: 'Bricolage Grotesque', sans-serif;
  font-size: 0.95rem;
  color: var(--text2);
  margin-bottom: 0.25rem;
}
.complete-hint {
  font-family: 'Fragment Mono', monospace;
  font-size: 0.78rem;
  color: var(--text3);
  margin-top: 1.75rem;
}
</style>
</head>
<body>

<div class="shell">

  <!-- ── Review mode ──────────────────────────────────────── -->
  <div id="review-view" style="display:none">
    <div class="header">
      <div class="titleblock">
        <div class="tb-cell"><div class="tb-label">drawing</div><div class="tb-val mono" id="doc-path"></div></div>
        <div class="tb-cell"><div class="tb-label">rev</div><div class="tb-val mono" id="round-badge"></div></div>
        <div class="tb-cell tb-flex"><div class="tb-label">title</div><div class="tb-val" id="doc-title"></div></div>
        <div class="tb-cell"><div class="tb-label">signed</div><div class="tb-val mono" id="r-progress-label">0 / 0</div></div>
      </div>
      <div class="progress-track">
        <div class="progress-fill" id="r-progress" style="width:0%"></div>
      </div>
    </div>
    <div class="ledger" id="ledger" style="display:none">
      <div class="ledger-head" id="ledger-head">
        <span class="ledger-title">Revisions &middot; <span id="ledger-count">0</span></span>
        <span class="ledger-chevron">&#9662;</span>
      </div>
      <div class="ledger-body-wrap">
        <div class="ledger-body-inner">
          <div class="ledger-rows" id="ledger-rows"></div>
        </div>
      </div>
    </div>
    <div class="cards" id="review-cards"></div>
  </div>

  <!-- ── Q&A mode ─────────────────────────────────────────── -->
  <div id="qa-view" style="display:none">
    <div class="header">
      <div class="titleblock">
        <div class="tb-cell"><div class="tb-label">phase</div><div class="tb-val mono">Q&amp;A</div></div>
        <div class="tb-cell tb-flex"><div class="tb-label">topic</div><div class="tb-val" id="qa-title"></div></div>
        <div class="tb-cell"><div class="tb-label">count</div><div class="tb-val mono" id="qa-count-badge"></div></div>
        <div class="tb-cell"><div class="tb-label">answered</div><div class="tb-val mono" id="qa-progress-label">0 / 0</div></div>
      </div>
      <div class="progress-track">
        <div class="progress-fill" id="qa-progress" style="width:0%"></div>
      </div>
    </div>
    <div class="cards" id="qa-cards"></div>
  </div>

  <!-- ── Processing state ─────────────────────────────────── -->
  <div id="processing-view" style="display:none">
    <div class="processing-inner">
      <div class="spinner"></div>
      <div class="processing-text">Claude is revising…</div>
    </div>
  </div>

  <!-- ── Complete state ───────────────────────────────────── -->
  <div id="complete-view" style="display:none">
    <div class="complete-inner">
      <div class="complete-check">&#10003;</div>
      <div class="complete-headline" id="complete-headline"></div>
      <div class="complete-detail" id="complete-detail"></div>
      <div class="ledger ledger-static" id="complete-ledger" style="display:none">
        <div class="ledger-head">
          <span class="ledger-title">Revisions &middot; <span id="complete-ledger-count">0</span></span>
        </div>
        <div class="ledger-rows" id="complete-ledger-rows"></div>
      </div>
      <div class="complete-hint">You can close this tab.</div>
    </div>
  </div>

</div>

<!-- Bottom bar -->
<div class="bottom-bar">
  <div class="bottom-inner">
    <div class="stats" id="stats-area">
      <span class="stat-approved" id="stat-approved"></span>
      <span class="stat-feedback" id="stat-feedback" style="display:none"></span>
      <span class="stat-pending"  id="stat-pending"></span>
    </div>
    <div class="btn-group">
      <button class="btn-skip" id="btn-skip">&#9889; skip rest &amp; submit</button>
      <button class="btn-submit disabled" id="btn-submit">submit all</button>
    </div>
  </div>
</div>

<script>
/* ─────────────────────────────────────────────────────────
   DATA
───────────────────────────────────────────────────────── */
let REVIEW_DATA = null;
let QA_DATA = null;

/* ─────────────────────────────────────────────────────────
   STATE
   Cards are built ONCE. All interactions do surgical DOM
   updates — no innerHTML rebuilds, no animation resets.
───────────────────────────────────────────────────────── */
const rState = { verdicts: {}, active: null };
const qState = { answers: {}, active: null };
const _pendingMarkdown = new Map(); // section id → raw markdown; deleted after first render

/* ─────────────────────────────────────────────────────────
   HELPERS
───────────────────────────────────────────────────────── */
function esc(s) {
  return String(s||'').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');
}

/* Render verbatim markdown into el. Falls back to raw monospace text
   if the CDN renderer didn't load (offline). */
function renderMarkdown(target, md) {
  if (window.marked) {
    const html = marked.parse(md);
    target.innerHTML = window.DOMPurify ? DOMPurify.sanitize(html) : html;
  } else {
    target.classList.add('md-raw');
    target.textContent = md;
  }
}

function el(id) { return document.getElementById(id); }

function ledgerRowsHTML(entries) {
  return entries.map(e => `
    <div class="ledger-row">
      <span class="ledger-round">R${esc(e.round)}</span>
      <span class="ledger-section">${esc(e.section_title)}</span>
      <span class="ledger-verdict v-${e.verdict}">${e.verdict}</span>
      <span class="ledger-note">${e.note ? '&ldquo;' + esc(e.note) + '&rdquo;' : '&mdash;'}</span>
    </div>`).join('');
}

function renderLedger() {
  const entries = (REVIEW_DATA && REVIEW_DATA.ledger) || [];
  const panel = el('ledger');
  if (!entries.length) { panel.style.display = 'none'; return; }
  panel.style.display = '';
  el('ledger-count').textContent = entries.length;
  el('ledger-rows').innerHTML = ledgerRowsHTML(entries);
  el('ledger').classList.toggle('is-collapsed', entries.length > 2);
  el('ledger-head').onclick = () => el('ledger').classList.toggle('is-collapsed');
}

/* ─────────────────────────────────────────────────────────
   REVIEW MODE — build once, update surgically
───────────────────────────────────────────────────────── */
function initReview() {
  _pendingMarkdown.clear();
  const container = el('review-cards');
  const priorApprovedSet = new Set(REVIEW_DATA.approved_ids || []);
  // Pre-populate approved state for sections approved in previous rounds
  priorApprovedSet.forEach(id => {
    rState.verdicts[id] = { verdict: 'approved', note: '' };
  });
  REVIEW_DATA.sections.forEach((s, i) => {
    const card = buildReviewCard(s);
    card.style.animationDelay = (0.04 + i * 0.04) + 's';
    container.appendChild(card);
    // Apply approved CSS immediately for pre-approved cards
    if (priorApprovedSet.has(s.id)) syncReviewCard(s.id);
  });
  // Open first non-approved card
  const firstPending = REVIEW_DATA.sections.find(s => !priorApprovedSet.has(s.id));
  if (firstPending) activateReviewCard(firstPending.id);
  else if (REVIEW_DATA.sections.length > 0) activateReviewCard(REVIEW_DATA.sections[0].id);
  updateReviewStats();
  renderLedger();
}

// Severity → CSS-slot whitelist. Anything off-list (or missing) renders as
// 'info' so a bad value can never break out of the class= attribute position.
const ANNOT_SEVERITIES = { info: 1, warn: 1, error: 1 };

// Build the advisory annotation strip for a card from section.annotations.
// Returns '' when there are none, so a bare section renders exactly as before.
function annotStripHTML(annotations) {
  if (!Array.isArray(annotations) || annotations.length === 0) return '';
  const rows = annotations.map(a => {
    a = a || {};
    const sev    = ANNOT_SEVERITIES[a.severity] ? a.severity : 'info';
    const kind   = esc(a.kind || 'note');
    const msg    = esc(a.message || '');
    const anchor = a.anchor ? ' title="' + esc(String(a.anchor)) + '"' : '';
    return '<div class="annot annot-' + sev + '"' + anchor + '>'
         + '<span class="annot-kind">' + kind + '</span>'
         + '<span class="annot-msg">' + msg + '</span></div>';
  }).join('');
  return '<div class="annot-strip" aria-label="pre-review annotations">' + rows + '</div>';
}

function buildReviewCard(section) {
  const card = document.createElement('div');
  card.className = 'card';
  card.id = 'rcard-' + section.id;

  // Store raw markdown for lazy render on first open
  _pendingMarkdown.set(section.id, section.content ?? section.excerpt ?? '');

  card.innerHTML = `
    <div class="card-head">
      <span class="dot dot-idle" id="rdot-${section.id}"></span>
      <div class="card-title-wrap">
        <div class="card-title">${esc(section.title)}</div>
        <div class="note-inline" id="rnote-inline-${section.id}" style="display:none"></div>
      </div>
      <span class="vbadge" id="rbadge-${section.id}" style="display:none"></span>
    </div>
    <div class="card-body-wrap">
      <div class="card-body-inner">
        <div class="card-body">
          ${annotStripHTML(section.annotations)}
          <div class="section-content" id="rcontent-${section.id}"></div>
          <div class="actions">
            <button class="action-btn" id="rbtn-approve-${section.id}">&#10003; approve</button>
            <button class="action-btn" id="rbtn-changes-${section.id}">&#8617; request changes</button>
            <button class="action-btn" id="rbtn-info-${section.id}">? need info</button>
            <button class="action-btn" id="rbtn-skip-${section.id}" style="margin-left:auto;opacity:0.55">&#8595; skip</button>
          </div>
          <div id="rnote-wrap-${section.id}" style="display:none; margin-top:2px">
            <textarea class="note-field" id="rnote-${section.id}" placeholder=""></textarea>
            <div class="thumb-strip" id="rthumbs-${section.id}" aria-live="polite" style="display:none"></div>
            <button type="button" class="attach-btn" id="rattach-${section.id}">&#128206; attach image</button>
            <input type="file" accept="image/*" multiple style="display:none" id="rfile-${section.id}">
          </div>
        </div>
      </div>
    </div>`;

  card.querySelector('.card-head').addEventListener('click', () => {
    toggleReviewCard(section.id);
  });

  card.querySelector('#rbtn-approve-' + section.id).addEventListener('click', e => { e.stopPropagation(); setReviewVerdict(section.id, 'approved'); });
  card.querySelector('#rbtn-changes-' + section.id).addEventListener('click', e => { e.stopPropagation(); setReviewVerdict(section.id, 'changes'); });
  card.querySelector('#rbtn-info-'    + section.id).addEventListener('click', e => { e.stopPropagation(); setReviewVerdict(section.id, 'info'); });
  card.querySelector('#rbtn-skip-'    + section.id).addEventListener('click', e => { e.stopPropagation(); skipReviewCard(section.id); });

  const rta = card.querySelector('#rnote-' + section.id);
  rta.addEventListener('input', e => {
    if (!rState.verdicts[section.id]) rState.verdicts[section.id] = {};
    rState.verdicts[section.id].note = e.target.value;
    syncNoteInline(section.id);
  });
  rta.addEventListener('click', e => e.stopPropagation());

  wireCapture(
    () => (rState.verdicts[section.id] ||= {}),
    card.querySelector('#rnote-' + section.id),
    card.querySelector('#rthumbs-' + section.id),
    card.querySelector('#rattach-' + section.id),
    card.querySelector('#rfile-' + section.id),
    card
  );

  return card;
}

function activateReviewCard(id) {
  // Deactivate previous
  if (rState.active && rState.active !== id) {
    el('rcard-' + rState.active)?.classList.remove('is-active');
    syncReviewDot(rState.active);
    syncNoteInline(rState.active);
  }
  rState.active = id;
  _ensureRendered(id);
  const card = el('rcard-' + id);
  if (card) {
    card.classList.add('is-active');
    card.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
  }
  syncReviewDot(id);
}

function _ensureRendered(id) {
  if (!_pendingMarkdown.has(id)) return;
  const contentEl = el('rcontent-' + id);
  if (!contentEl) return;
  renderMarkdown(contentEl, _pendingMarkdown.get(id));
  _pendingMarkdown.delete(id);
}

function skipReviewCard(id) {
  el('rcard-' + id)?.classList.remove('is-active');
  rState.active = null;
  syncReviewDot(id);
  const sections = REVIEW_DATA.sections;
  const idx = sections.findIndex(s => s.id === id);
  const rest = [...sections.slice(idx + 1), ...sections.slice(0, idx)];
  const next = rest.find(s => !rState.verdicts[s.id]?.verdict);
  if (next) setTimeout(() => activateReviewCard(next.id), 80);
}

function toggleReviewCard(id) {
  if (rState.active === id) {
    el('rcard-' + id)?.classList.remove('is-active');
    rState.active = null;
    syncReviewDot(id);
    syncNoteInline(id);
  } else {
    activateReviewCard(id);
  }
}

function setReviewVerdict(id, verdict) {
  const prev = rState.verdicts[id]?.verdict;

  // Toggle off same verdict — clear only the verdict, keeping any attached
  // images and note text so a mis-click doesn't silently discard them.
  if (prev === verdict) {
    if (rState.verdicts[id]) rState.verdicts[id].verdict = undefined;
    syncReviewCard(id);
    updateReviewStats();
    return;
  }

  if (!rState.verdicts[id]) rState.verdicts[id] = {};
  rState.verdicts[id].verdict = verdict;

  if (verdict === 'approved') {
    el('rcard-' + id)?.classList.remove('is-active');
    el('rcard-' + id)?.classList.add('is-approved');
    rState.active = null;
    // Auto-advance to next unreviewed
    const sections = REVIEW_DATA.sections;
    const idx = sections.findIndex(s => s.id === id);
    const next = sections.slice(idx + 1).find(s => rState.verdicts[s.id]?.verdict !== 'approved');
    if (next) setTimeout(() => activateReviewCard(next.id), 80);
  }

  syncReviewCard(id);
  updateReviewStats();
}

function syncReviewCard(id) {
  const verdict = rState.verdicts[id]?.verdict || null;
  const note    = rState.verdicts[id]?.note    || '';

  // Approved dimming
  el('rcard-' + id)?.classList.toggle('is-approved', verdict === 'approved');

  // Dot
  syncReviewDot(id);

  // Badge
  const badge = el('rbadge-' + id);
  if (verdict === 'approved') { badge.style.display=''; badge.className='vbadge vbadge-approved'; badge.textContent='approved'; }
  else if (verdict === 'changes') { badge.style.display=''; badge.className='vbadge vbadge-changes'; badge.textContent='changes'; }
  else if (verdict === 'info')    { badge.style.display=''; badge.className='vbadge vbadge-info';    badge.textContent='needs info'; }
  else badge.style.display = 'none';

  // Action button active states
  el('rbtn-approve-' + id).className = 'action-btn' + (verdict === 'approved' ? ' sel-approve' : '');
  el('rbtn-changes-' + id).className = 'action-btn' + (verdict === 'changes'  ? ' sel-changes' : '');
  el('rbtn-info-'    + id).className = 'action-btn' + (verdict === 'info'     ? ' sel-info'    : '');

  // Textarea show/hide
  const noteWrap = el('rnote-wrap-' + id);
  const ta = el('rnote-' + id);
  if (verdict === 'changes' || verdict === 'info') {
    noteWrap.style.display = '';
    ta.placeholder = verdict === 'changes'
      ? 'Describe what needs to change… or paste a screenshot'
      : 'What do you need to know? — or paste a screenshot';
    if (ta.value !== note) ta.value = note;
    ta.focus();
  } else {
    noteWrap.style.display = 'none';
  }

  syncNoteInline(id);
}

function syncReviewDot(id) {
  const verdict  = rState.verdicts[id]?.verdict;
  const isActive = rState.active === id;
  const dot = el('rdot-' + id);
  if (!dot) return;
  dot.className = 'dot ' + (
    verdict === 'approved' ? 'dot-approved' :
    verdict === 'changes'  ? 'dot-changes'  :
    verdict === 'info'     ? 'dot-info'     :
    isActive               ? 'dot-active'   : 'dot-idle'
  );
}

function syncNoteInline(id) {
  const verdict = rState.verdicts[id]?.verdict;
  const note    = rState.verdicts[id]?.note || '';
  const inlineEl = el('rnote-inline-' + id);
  if (!inlineEl) return;
  const show = note && verdict && verdict !== 'approved' && rState.active !== id;
  inlineEl.style.display = show ? '' : 'none';
  if (show) { inlineEl.textContent = note; inlineEl.title = note; }
}

function updateReviewStats() {
  const sections = REVIEW_DATA.sections;
  const approved    = sections.filter(s => rState.verdicts[s.id]?.verdict === 'approved').length;
  const withFeedback= sections.filter(s => ['changes','info'].includes(rState.verdicts[s.id]?.verdict)).length;
  const total    = sections.length;
  const reviewed = approved + withFeedback;
  const remaining= total - reviewed;

  el('r-progress').style.width = (reviewed / total * 100) + '%';
  el('r-progress-label').textContent = `${reviewed} / ${total}`;
  el('stat-approved').textContent = `${approved} approved`;
  const fEl = el('stat-feedback');
  if (withFeedback > 0) { fEl.style.display=''; fEl.textContent=`${withFeedback} with feedback`; }
  else fEl.style.display = 'none';
  el('stat-pending').textContent = remaining > 0 ? `${remaining} unreviewed` : 'all reviewed';

  const sub = el('btn-submit');
  if (remaining === 0 && reviewed > 0) { sub.className='btn-submit ready';    sub.textContent='submit all'; }
  else                                 { sub.className='btn-submit disabled'; sub.textContent=remaining>0?`submit all (${remaining} remaining)`:'submit all'; }
}

/* ─────────────────────────────────────────────────────────
   Q&A MODE — build once, update surgically
───────────────────────────────────────────────────────── */
function initQA() {
  const container = el('qa-cards');
  QA_DATA.questions.forEach((q, i) => {
    const card = buildQACard(q);
    card.style.animationDelay = (0.04 + i * 0.04) + 's';
    container.appendChild(card);
  });
  if (QA_DATA.questions.length > 0) {
    activateQACard(QA_DATA.questions[0].id);
  }
  updateQAStats();
}

function buildQACard(q) {
  const card = document.createElement('div');
  card.className = 'card';
  card.id = 'qacard-' + q.id;

  const choicesHtml = q.choices.map(c =>
    `<button class="choice-chip" data-choice="${esc(c)}">${esc(c)}</button>`
  ).join('');

  card.innerHTML = `
    <div class="card-head">
      <span class="dot dot-idle" id="qdot-${q.id}"></span>
      <div class="card-title-wrap">
        <div class="card-title">${esc(q.text)}</div>
      </div>
      <span class="vbadge vbadge-approved" id="qbadge-${q.id}" style="display:none"></span>
    </div>
    <div class="card-body-wrap">
      <div class="card-body-inner">
        <div class="card-body">
          <p class="section-summary">${esc(q.hint || '')}</p>
          <div class="choices-label">Choices</div>
          <div class="choices" id="qchoices-${q.id}">${choicesHtml}</div>
          <textarea class="note-field" id="qnote-${q.id}" placeholder="Add context (optional) — or paste a screenshot"></textarea>
          <div class="thumb-strip" id="qthumbs-${q.id}" aria-live="polite" style="display:none"></div>
          <button type="button" class="attach-btn" id="qattach-${q.id}">&#128206; attach image</button>
          <input type="file" accept="image/*" multiple style="display:none" id="qfile-${q.id}">
          <div class="qa-actions">
            <button class="qa-btn" id="qconfirm-${q.id}">&#10003; confirm</button>
            <button class="qa-btn" id="qskip-${q.id}">&#8595; skip</button>
          </div>
        </div>
      </div>
    </div>`;

  card.querySelector('.card-head').addEventListener('click', () => toggleQACard(q.id));

  card.querySelector('#qchoices-' + q.id).addEventListener('click', e => {
    const chip = e.target.closest('.choice-chip');
    if (!chip) return;
    e.stopPropagation();
    if (!qState.answers[q.id]) qState.answers[q.id] = {};
    const ch = chip.dataset.choice;
    qState.answers[q.id].choice = qState.answers[q.id].choice === ch ? null : ch;
    syncQACard(q.id);
    updateQAStats();
  });

  const qta = card.querySelector('#qnote-' + q.id);
  qta.addEventListener('input', e => {
    if (!qState.answers[q.id]) qState.answers[q.id] = {};
    qState.answers[q.id].note = e.target.value;
  });
  qta.addEventListener('click', e => e.stopPropagation());

  card.querySelector('#qconfirm-' + q.id).addEventListener('click', e => { e.stopPropagation(); advanceQA(q.id); });
  card.querySelector('#qskip-'   + q.id).addEventListener('click', e => { e.stopPropagation(); advanceQA(q.id); });

  wireCapture(
    () => (qState.answers[q.id] ||= {}),
    card.querySelector('#qnote-' + q.id),
    card.querySelector('#qthumbs-' + q.id),
    card.querySelector('#qattach-' + q.id),
    card.querySelector('#qfile-' + q.id),
    card
  );

  return card;
}

function activateQACard(id) {
  if (qState.active && qState.active !== id) {
    el('qacard-' + qState.active)?.classList.remove('is-active');
    syncQADot(qState.active);
  }
  qState.active = id;
  const card = el('qacard-' + id);
  if (card) {
    card.classList.add('is-active');
    card.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
  }
  syncQADot(id);
}

function toggleQACard(id) {
  if (qState.active === id) {
    el('qacard-' + id)?.classList.remove('is-active');
    qState.active = null;
    syncQADot(id);
  } else {
    activateQACard(id);
  }
}

function advanceQA(id) {
  el('qacard-' + id)?.classList.remove('is-active');
  if (qState.answers[id]?.choice) el('qacard-' + id)?.classList.add('is-approved');
  qState.active = null;
  syncQADot(id);

  const qs  = QA_DATA.questions;
  const idx = qs.findIndex(q => q.id === id);
  const next= qs.slice(idx + 1).find(q => !qState.answers[q.id]?.choice);
  if (next) setTimeout(() => activateQACard(next.id), 80);

  updateQAStats();
}

function syncQACard(id) {
  const choice = qState.answers[id]?.choice || null;

  // Chip selections
  el('qchoices-' + id).querySelectorAll('.choice-chip').forEach(chip => {
    chip.classList.toggle('selected', chip.dataset.choice === choice);
  });

  // Badge
  const badge = el('qbadge-' + id);
  if (choice) { badge.style.display=''; badge.textContent=choice; }
  else badge.style.display = 'none';

  // Confirm button highlight
  el('qconfirm-' + id).className = 'qa-btn' + (choice ? ' confirm' : '');

  syncQADot(id);
}

function syncQADot(id) {
  const choice   = qState.answers[id]?.choice;
  const isActive = qState.active === id;
  const dot = el('qdot-' + id);
  if (!dot) return;
  dot.className = 'dot ' + (choice ? 'dot-approved' : isActive ? 'dot-active' : 'dot-idle');
}

function updateQAStats() {
  const qs       = QA_DATA.questions;
  const answered = qs.filter(q => qState.answers[q.id]?.choice).length;
  const total    = qs.length;
  const remaining= total - answered;

  el('qa-progress').style.width = (answered / total * 100) + '%';
  el('qa-progress-label').textContent = `${answered} / ${total}`;
  el('stat-approved').textContent = `${answered} answered`;
  el('stat-feedback').style.display = 'none';
  el('stat-pending').textContent = remaining > 0 ? `${remaining} remaining` : 'all answered';

  const sub = el('btn-submit');
  if (remaining === 0) { sub.className='btn-submit ready';    sub.textContent='done →'; }
  else                 { sub.className='btn-submit disabled'; sub.textContent=`done (${remaining} remaining)`; }
}

/* ─── Image attachments ────────────────────────────────────── */
function renderThumbs(stateObj, stripEl) {
  const imgs = stateObj.images || [];
  stripEl.innerHTML = imgs.map((im, i) =>
    `<div class="thumb"><img src="data:${esc(im.mime)};base64,${im.data}" width="64" height="64" alt="Attached image ${i + 1}">` +
    `<button class="thumb-remove" data-i="${i}" title="Remove image" aria-label="Remove image">&times;</button></div>`
  ).join('');
  stripEl.style.display = imgs.length ? 'flex' : 'none';
  stripEl.querySelectorAll('.thumb-remove').forEach(btn => {
    btn.addEventListener('click', e => {
      e.stopPropagation();
      stateObj.images.splice(Number(btn.dataset.i), 1);
      renderThumbs(stateObj, stripEl);
    });
  });
}

function attachImageFiles(stateObj, files, stripEl) {
  const list = Array.from(files || []).filter(f => f.type.startsWith('image/'));
  if (!list.length) return;
  if (!stateObj.images) stateObj.images = [];
  let pending = list.length;
  list.forEach(file => {
    const reader = new FileReader();
    reader.onload = () => {
      const result = String(reader.result);
      const comma = result.indexOf(',');
      stateObj.images.push({ data: result.slice(comma + 1), mime: file.type });
      if (--pending === 0) renderThumbs(stateObj, stripEl);
    };
    reader.onerror = () => { if (--pending === 0) renderThumbs(stateObj, stripEl); };
    reader.readAsDataURL(file);
  });
}

function wireCapture(stateGetter, textarea, stripEl, attachBtn, fileInput, card) {
  // Only accept drops when the note area holding the strip is visible — review
  // cards hide it for approved/pending verdicts, where captured images could
  // neither be seen, removed, nor read by the verdict's consumer.
  const droppable = () => stripEl.parentElement.style.display !== 'none';
  textarea.addEventListener('paste', e => {
    const files = Array.from(e.clipboardData?.items || [])
      .filter(it => it.kind === 'file' && it.type.startsWith('image/'))
      .map(it => it.getAsFile()).filter(Boolean);
    if (files.length) { e.preventDefault(); attachImageFiles(stateGetter(), files, stripEl); }
  });
  card.addEventListener('dragover', e => {
    if (!droppable()) return;
    e.preventDefault();
    card.classList.add('is-drop-target');
  });
  card.addEventListener('dragleave', e => {
    if (e.target === card) card.classList.remove('is-drop-target');
  });
  card.addEventListener('drop', e => {
    card.classList.remove('is-drop-target');
    if (!droppable() || !e.dataTransfer?.files?.length) return;
    e.preventDefault();
    attachImageFiles(stateGetter(), e.dataTransfer.files, stripEl);
  });
  attachBtn.addEventListener('click', e => { e.stopPropagation(); fileInput.click(); });
  fileInput.addEventListener('change', () => {
    attachImageFiles(stateGetter(), fileInput.files, stripEl);
    fileInput.value = '';
  });
}

/* ─── Submit handlers ──────────────────────────────────────── */
function submitReview(early) {
  el('btn-skip').disabled   = true;
  el('btn-submit').disabled = true;
  const result = {
    round: REVIEW_DATA.round,
    submitted_early: early,
    sections: REVIEW_DATA.sections.map(s => {
      const v = rState.verdicts[s.id] || {};
      return { id: s.id, verdict: v.verdict || 'pending', note: v.note || '',
               ...(v.images && v.images.length && { images: v.images }) };
    })
  };
  fetch('/submit', {
    method:  'POST',
    headers: { 'Content-Type': 'application/json' },
    body:    JSON.stringify(result)
  }).catch(err => alert('Submit failed: ' + (err.message || 'network error')));
}

function submitQA(early) {
  el('btn-skip').disabled   = true;
  el('btn-submit').disabled = true;
  // Images on a question with no selected choice would be silently dropped by
  // the choice filter below — warn before discarding them.
  if (!early) {
    const orphaned = QA_DATA.questions.filter(
      q => qState.answers[q.id]?.images?.length && !qState.answers[q.id]?.choice
    );
    if (orphaned.length &&
        !confirm(orphaned.length + ' question(s) have an attached image but no selected choice — their images will be dropped. Continue?')) {
      el('btn-skip').disabled   = false;
      el('btn-submit').disabled = false;
      return;
    }
  }
  const result = {
    answers: QA_DATA.questions
      .filter(q => qState.answers[q.id]?.choice)
      .map(q => {
        const a = qState.answers[q.id];
        return { id: q.id, choice: a.choice, note: a.note || '',
                 ...(a.images && a.images.length && { images: a.images }) };
      }),
    skipped: early
  };
  fetch('/submit', {
    method:  'POST',
    headers: { 'Content-Type': 'application/json' },
    body:    JSON.stringify(result)
  }).catch(err => alert('Submit failed: ' + (err.message || 'network error')));
}

el('btn-skip').addEventListener('click', () => {
  if (REVIEW_DATA) submitReview(true);
  else             submitQA(true);
});

el('btn-submit').addEventListener('click', () => {
  if (el('btn-submit').classList.contains('disabled')) return;
  if (REVIEW_DATA) submitReview(false);
  else             submitQA(false);
});

/* ─── Init — fetch data from server, then build cards ─── */
/* ─── SSE client ────────────────────────────────────────── */
function connectSSE() {
  const es = new EventSource('/events');

  es.addEventListener('processing', () => {
    el('review-view').style.display     = 'none';
    el('qa-view').style.display         = 'none';
    el('processing-view').style.display = '';
  });

  es.addEventListener('round', e => {
    const data = JSON.parse(e.data);
    REVIEW_DATA       = data;
    rState.verdicts   = {};
    rState.active     = null;
    el('round-badge').textContent = String(data.round).padStart(2, '0');
    el('review-cards').innerHTML  = '';
    initReview();
    el('processing-view').style.display = 'none';
    el('review-view').style.display     = '';
    el('btn-skip').disabled   = false;
    el('btn-submit').disabled = false;
  });

  es.addEventListener('complete', e => {
    es.close(); // prevent onerror when server shuts down 2s later
    const data = JSON.parse(e.data);
    el('processing-view').style.display = 'none';
    el('review-view').style.display     = 'none';
    el('qa-view').style.display         = 'none';
    el('complete-view').style.display   = '';
    const r   = data.rounds_total     != null ? data.rounds_total    : '?';
    const s   = data.sections_total   != null ? data.sections_total  : '?';
    const rev = data.sections_revised != null ? data.sections_revised : null;
    el('complete-headline').textContent = `Signed off — ${s} section${s !== 1 ? 's' : ''} across ${r} round${r !== 1 ? 's' : ''}`;
    el('complete-detail').textContent   = rev != null
      ? `${rev} section${rev !== 1 ? 's' : ''} revised`
      : '';
    const entries = (REVIEW_DATA && REVIEW_DATA.ledger) || [];
    if (entries.length) {
      el('complete-ledger').style.display = '';
      el('complete-ledger-count').textContent = entries.length;
      el('complete-ledger-rows').innerHTML = ledgerRowsHTML(entries);
    }
    document.querySelector('.bottom-bar').style.display = 'none';
  });

  es.onerror = () => {
    if (!el('sse-error-banner')) {
      const b = document.createElement('div');
      b.id = 'sse-error-banner';
      b.style.cssText = 'position:fixed;top:0;left:0;right:0;padding:0.6rem 1rem;background:#ef4444;color:#fff;font-family:monospace;font-size:0.82rem;z-index:1000;text-align:center';
      b.textContent = 'Connection lost — check the terminal.';
      document.body.prepend(b);
    }
  };
}

/* ─── Keyboard shortcuts ────────────────────────────────── */
document.addEventListener('keydown', e => {
  const tag = document.activeElement?.tagName;
  if (tag === 'TEXTAREA' || tag === 'INPUT') return;

  if (REVIEW_DATA) {
    if (e.key === 'a' && rState.active) { e.preventDefault(); setReviewVerdict(rState.active, 'approved'); return; }
    if (e.key === 'c' && rState.active) { e.preventDefault(); setReviewVerdict(rState.active, 'changes'); return; }
    if (e.key === 'i' && rState.active) { e.preventDefault(); setReviewVerdict(rState.active, 'info'); return; }
    if (e.key === 'Tab') {
      e.preventDefault();
      if (rState.active) {
        skipReviewCard(rState.active);
      } else {
        const first = REVIEW_DATA.sections.find(s => !rState.verdicts[s.id]?.verdict);
        if (first) activateReviewCard(first.id);
      }
      return;
    }
    if ((e.metaKey || e.ctrlKey) && e.key === 'Enter') {
      const sub = el('btn-submit');
      if (sub.classList.contains('ready') && !sub.disabled) { e.preventDefault(); sub.click(); }
      return;
    }
  }

  if (QA_DATA && qState.active) {
    const q = QA_DATA.questions.find(q => q.id === qState.active);
    if (q) {
      const n = parseInt(e.key, 10);
      if (!isNaN(n) && n >= 1 && n <= q.choices.length) {
        e.preventDefault();
        const choice = q.choices[n - 1];
        if (!qState.answers[qState.active]) qState.answers[qState.active] = {};
        qState.answers[qState.active].choice =
          qState.answers[qState.active].choice === choice ? null : choice;
        syncQACard(qState.active);
        updateQAStats();
        return;
      }
    }
    if (e.key === 'Tab') { e.preventDefault(); advanceQA(qState.active); return; }
    if ((e.metaKey || e.ctrlKey) && e.key === 'Enter') {
      const sub = el('btn-submit');
      if (sub.classList.contains('ready') && !sub.disabled) { e.preventDefault(); sub.click(); }
      return;
    }
  }
});

document.addEventListener('DOMContentLoaded', () => {
  el('btn-skip').disabled   = true;
  el('btn-submit').disabled = true;

  fetch('/input')
    .then(r => r.json())
    .then(data => {
      el('btn-skip').disabled   = false;
      el('btn-submit').disabled = false;

      if (data.mode === 'review') {
        REVIEW_DATA = data;
        el('doc-path').textContent    = data.doc_file || '';
        el('doc-title').innerHTML     = 'viva <em>review</em>';
        el('round-badge').textContent = String(data.round).padStart(2, '0');
        el('review-view').style.display = '';
        initReview();
        connectSSE();
      } else {
        QA_DATA = data;
        el('qa-title').innerHTML          = esc(data.context || 'Q&amp;A phase');
        el('qa-count-badge').textContent  = `${data.questions.length} questions`;
        el('qa-view').style.display = '';
        initQA();
        connectSSE();
      }
    })
    .catch(err => {
      document.body.innerHTML = '<p style="padding:2rem;font-family:sans-serif;color:#f87171">Failed to load session data: ' + (err.message || 'network error') + '</p>';
    });
});
</script>
</body>
</html>"""

_HTML_BYTES = HTML.encode()

_shutdown = threading.Event()
_input_data: dict = {}
_output_path: str = ""
_server_state: str = "reviewing"
_sse_clients: list = []
_clients_lock = threading.Lock()
_data_lock = threading.Lock()
_ledger: list = []


class ThreadedHTTPServer(ThreadingMixIn, HTTPServer):
    daemon_threads = True


def _push_sse(event: str, data: dict) -> None:
    msg = f"event: {event}\ndata: {json.dumps(data)}\n\n".encode()
    with _clients_lock:
        dead = []
        for wfile in _sse_clients:
            try:
                wfile.write(msg)
                wfile.flush()
            except (IOError, OSError):
                dead.append(wfile)
        for wfile in dead:
            _sse_clients.remove(wfile)


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="viva review server")
    p.add_argument("--mode",       required=True, choices=["review", "qa"])
    p.add_argument("--input",      required=True)
    p.add_argument("--output",     required=True)
    p.add_argument("--no-browser", action="store_true", help="Skip opening browser (for testing)")
    return p.parse_args()


def find_free_port() -> int:
    with socket.socket() as s:
        s.bind(("", 0))
        return s.getsockname()[1]


def load_input(path: str) -> dict:
    with open(path, encoding='utf-8') as f:
        return json.load(f)


def _atomic_write(path: Path, text: str) -> None:
    # A reader polling with `[ -f path ]` then `cat path` must never observe a
    # truncated/partial file. Write a sibling tmp, then rename atomically.
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    with open(tmp, "w", encoding='utf-8') as f:
        f.write(text)
    os.replace(tmp, path)


def write_output(path: str, data: dict) -> None:
    _atomic_write(Path(path), json.dumps(data, indent=2))


# Raster formats only — SVG is excluded deliberately because it can carry
# embedded JavaScript. The MIME is also the sole source of the on-disk
# extension, so this allowlist doubles as the extension allowlist.
ALLOWED_IMAGE_MIMES = {
    "image/png": "png",
    "image/jpeg": "jpg",
    "image/gif": "gif",
    "image/webp": "webp",
}
MAX_IMAGE_BYTES = 10 * 1024 * 1024  # 10 MiB decoded, per image
MAX_SUBMIT_BYTES = 256 * 1024 * 1024  # 256 MiB total /submit request body


def extract_attachments(data: dict, output_path: str, rnd: int) -> dict:
    """Turn inline base64 `images` on each submitted item into written files.

    Walks review `sections` and Q&A `answers`. For each image: validates the
    declared MIME against ALLOWED_IMAGE_MIMES, base64-decodes the data,
    enforces MAX_IMAGE_BYTES, and writes it under `<output dir>/attachments/`
    with a SERVER-GENERATED filename `r{rnd}-{safeId}-{i}.{ext}`. Surviving
    paths are collected into the item's `attachments` list. Invalid, oversized,
    or undecodable images are dropped silently. The `images` key is always
    removed. Mutates and returns `data`.
    """
    attach_dir = Path(output_path).parent / "attachments"
    items = list(data.get("sections", [])) + list(data.get("answers", []))
    for item in items:
        if not isinstance(item, dict):
            continue
        images = item.pop("images", None)
        if not isinstance(images, list):
            continue
        # Section/question ids are sequential (s1, q1, …), so sanitized names do
        # not collide; the sub() only neutralizes path separators in the id.
        safe_id = re.sub(r"[^A-Za-z0-9_-]", "_", str(item.get("id", "x"))) or "x"
        paths = []
        for i, img in enumerate(images):
            if not isinstance(img, dict):
                continue
            ext = ALLOWED_IMAGE_MIMES.get(img.get("mime"))
            if ext is None:
                continue
            try:
                raw = base64.b64decode(img.get("data", ""), validate=True)
            except (ValueError, TypeError):
                continue
            if not raw or len(raw) > MAX_IMAGE_BYTES:
                continue
            attach_dir.mkdir(parents=True, exist_ok=True)
            dest = attach_dir / f"r{rnd}-{safe_id}-{i}.{ext}"
            try:
                dest.write_bytes(raw)
            except OSError as e:
                print(f"viva · warning: could not write attachment {dest}: {e}",
                      file=sys.stderr, flush=True)
                continue
            paths.append(str(dest))
        if paths:
            item["attachments"] = paths
    return data


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args) -> None:
        pass  # silence access log

    def do_GET(self) -> None:
        path = urlparse(self.path).path
        if path in ("/", ""):
            self._send(200, "text/html; charset=utf-8", _HTML_BYTES)
        elif path == "/input":
            with _data_lock:
                body = json.dumps({**_input_data, "ledger": _ledger}).encode()
            self._send(200, "application/json", body)
        elif path == "/events":
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("Connection", "keep-alive")
            self.end_headers()
            try:
                with _clients_lock:
                    _sse_clients.append(self.wfile)
                _shutdown.wait()
            except Exception:
                pass
            finally:
                with _clients_lock:
                    try:
                        _sse_clients.remove(self.wfile)
                    except ValueError:
                        pass
        else:
            self._send(404, "text/plain", b"not found")

    def do_POST(self) -> None:
        global _input_data, _output_path, _server_state
        parsed = urlparse(self.path)
        path   = parsed.path
        params = parse_qs(parsed.query)
        if path == "/submit":
            # Loopback-only tool: reject cross-origin POSTs (defense-in-depth
            # against a malicious page driving the local write sink via CSRF).
            origin = self.headers.get("Origin", "")
            if origin and not (origin.startswith("http://127.0.0.1")
                               or origin.startswith("http://localhost")):
                self._send(403, "text/plain", b"forbidden origin")
                return
            try:
                length = int(self.headers.get("Content-Length", 0))
            except ValueError:
                self._send(400, "text/plain", b"invalid Content-Length")
                return
            if length > MAX_SUBMIT_BYTES:
                self._send(413, "text/plain", b"payload too large")
                return

            body = self.rfile.read(length)

            try:
                data = json.loads(body)
            except json.JSONDecodeError:
                self._send(400, "application/json", b'{"error":"invalid json"}')
                return

            with _data_lock:
                out = _output_path
                titles = {s.get("id"): s.get("title", "")
                          for s in _input_data.get("sections", [])}
                try:
                    rnd = int(data.get("round", _input_data.get("round", 0)))
                except (TypeError, ValueError):
                    rnd = 0
                for s in data.get("sections", []):
                    if s.get("verdict") in ("changes", "info"):
                        _ledger.append({
                            "round": rnd,
                            "section_title": titles.get(s.get("id"), s.get("id", "?")),
                            "verdict": s["verdict"],
                            "note": s.get("note", ""),
                        })
            data = extract_attachments(data, out, rnd)
            try:
                write_output(out, data)
            except (IOError, OSError) as e:
                self._send(500, "application/json", f'{{"error":"write failed: {e}"}}'.encode())
                return

            self._send(200, "application/json", b'{"ok":true}')
            _server_state = "processing"
            _push_sse("processing", {})
        elif path == "/next-round":
            output = params.get("output", [None])[0]
            if not output:
                self._send(400, "text/plain", b"missing ?output= param")
                return
            try:
                length = int(self.headers.get("Content-Length", 0))
            except ValueError:
                self._send(400, "text/plain", b"invalid Content-Length")
                return
            body = self.rfile.read(length)
            try:
                new_data = json.loads(body)
            except json.JSONDecodeError:
                self._send(400, "application/json", b'{"error":"invalid json"}')
                return
            with _data_lock:
                _input_data = new_data
                _output_path = output
                ledger_snapshot = list(_ledger)
            _server_state = "reviewing"
            self._send(200, "application/json", b'{"ok":true}')
            _push_sse("round", {**new_data, "ledger": ledger_snapshot})
        elif path == "/complete":
            try:
                length = int(self.headers.get("Content-Length", 0))
            except ValueError:
                self._send(400, "text/plain", b"invalid Content-Length")
                return
            body = self.rfile.read(length) if length else b'{}'
            try:
                summary = json.loads(body) if body.strip() else {}
            except json.JSONDecodeError:
                summary = {}
            _server_state = "complete"
            self._send(200, "application/json", b'{"ok":true}')
            _push_sse("complete", summary)
            threading.Timer(2.0, _shutdown.set).start()
        else:
            self._send(404, "text/plain", b"not found")

    def _send(self, status: int, content_type: str, body: bytes) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


if __name__ == "__main__":
    args = parse_args()
    signal.signal(signal.SIGINT, lambda *_: _shutdown.set())
    _input_data = load_input(args.input)
    _output_path = args.output

    port = find_free_port()
    server = ThreadedHTTPServer(("127.0.0.1", port), Handler)
    server.timeout = 0.5
    url = f"http://127.0.0.1:{port}"

    url_file = Path(args.output).parent / "server.url"
    _atomic_write(url_file, url)
    print(f"viva · {args.mode} mode · {url}", flush=True)

    if not args.no_browser:
        threading.Thread(target=webbrowser.open, args=(url,), daemon=True).start()

    try:
        while not _shutdown.is_set():
            server.handle_request()
    finally:
        url_file.unlink(missing_ok=True)
        server.server_close()
        print("viva · done", flush=True)
