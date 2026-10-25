"""Public landing page, role-based sign-in, employee workspace, and admin portal."""

import html
import json
import os
import re
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path

import streamlit as st

from policypilot.app.auth_api import MeResponse
from policypilot.app.models.domain import Permission, SystemRole
from policypilot.ui.client import (
    AdminOverview,
    ApiClient,
    DashboardError,
    QueryAnswer,
    QueryCitation,
)

_CSS = r"""
<style>
:root {
  --pp-ink: #0b1f1e;
  --pp-teal: #0f766e;
  --pp-teal-bright: #0d9488;
  --pp-mint: #ccfbf1;
  --pp-slate: #475569;
  --pp-line: #dbe7e6;
  --pp-paper: #ffffff;
  --pp-soft: #f1f7f6;
}
html, body, [class*="css"] {
  font-family: ui-sans-serif, system-ui, -apple-system, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
}
[data-testid="stAppViewContainer"] {
  background:
    radial-gradient(1100px 500px at 8% -5%, rgba(13,148,136,.14), transparent 60%),
    radial-gradient(900px 500px at 95% 4%, rgba(15,118,110,.10), transparent 55%),
    linear-gradient(180deg, #f7fbfa 0%, #f3f8f7 45%, #f8fafc 100%);
}
[data-testid="stHeader"] { background: transparent; }
[data-testid="stToolbar"] { display: none; }
#MainMenu, footer { visibility: hidden; }
.block-container { max-width: 1180px; padding: 1.1rem clamp(1rem, 2.5vw, 2rem) 3.5rem; }

.pp-nav {
  display: flex; align-items: center; justify-content: space-between;
  padding: .55rem 0 1.3rem; color: var(--pp-ink);
}
.pp-brand { display: flex; align-items: center; gap: .65rem; font-weight: 800; letter-spacing: -.02em; }
.pp-logo {
  display: inline-flex; align-items: center; justify-content: center;
  width: 32px; height: 32px; border-radius: 10px; color: white;
  background: linear-gradient(135deg, #0f766e, #0d9488);
  box-shadow: 0 8px 18px rgba(15,118,110,.28); font-size: .95rem;
}
.pp-nav-meta {
  display: inline-flex; align-items: center; gap: .45rem;
  color: var(--pp-slate); font-size: .78rem; font-weight: 650;
  background: rgba(255,255,255,.75); border: 1px solid var(--pp-line);
  padding: .35rem .65rem; border-radius: 999px;
}
.pp-dot { width: 7px; height: 7px; border-radius: 50%; background: #10b981; box-shadow: 0 0 0 4px rgba(16,185,129,.12); }

.pp-hero {
  position: relative; overflow: hidden; color: #ecfdf5;
  background:
    radial-gradient(600px 260px at 12% 0%, rgba(45,212,191,.30), transparent 65%),
    radial-gradient(500px 240px at 95% 20%, rgba(56,189,248,.12), transparent 60%),
    linear-gradient(135deg, #062f2c 0%, #0a3d39 48%, #0b1f2a 100%);
  border: 1px solid rgba(255,255,255,.10);
  border-radius: 30px; padding: clamp(2rem, 5vw, 4.25rem);
  box-shadow: 0 30px 70px rgba(6,47,44,.25);
}
.pp-hero:before {
  content: ""; position: absolute; inset: 0; pointer-events: none;
  background-image:
    linear-gradient(rgba(255,255,255,.045) 1px, transparent 1px),
    linear-gradient(90deg, rgba(255,255,255,.045) 1px, transparent 1px);
  background-size: 46px 46px;
  mask-image: linear-gradient(180deg, rgba(0,0,0,.75), transparent 78%);
}
.pp-eyebrow {
  position: relative; display: inline-flex; align-items: center; gap: .5rem;
  font-size: .72rem; font-weight: 750; letter-spacing: .14em; text-transform: uppercase;
  color: #99f6e4; background: rgba(204,251,241,.08); border: 1px solid rgba(153,246,228,.22);
  padding: .42rem .7rem; border-radius: 999px;
}
.pp-pulse {
  width: 7px; height: 7px; border-radius: 50%; background: #5eead4;
  box-shadow: 0 0 0 0 rgba(94,234,212,.55); animation: ppPulse 2.2s infinite;
}
@keyframes ppPulse {
  0% { box-shadow: 0 0 0 0 rgba(94,234,212,.45); }
  70% { box-shadow: 0 0 0 8px rgba(94,234,212,0); }
  100% { box-shadow: 0 0 0 0 rgba(94,234,212,0); }
}
.pp-hero h1 {
  position: relative; max-width: 850px; margin: 1.15rem 0 .9rem;
  font-size: clamp(2.3rem, 5vw, 4.4rem); line-height: 1.01; letter-spacing: -.055em;
  font-weight: 850; color: #ffffff;
}
.pp-gradient {
  background: linear-gradient(90deg, #5eead4, #99f6e4 55%, #bae6fd);
  -webkit-background-clip: text; background-clip: text; color: transparent;
}
.pp-lead {
  position: relative; max-width: 690px; color: rgba(236,253,245,.78);
  font-size: clamp(1rem, 1.4vw, 1.12rem); line-height: 1.65; margin: 0 0 1.65rem;
}
.pp-stats { position: relative; display: grid; grid-template-columns: repeat(4, minmax(0,1fr)); gap: .7rem; max-width: 850px; }
.pp-stat {
  background: rgba(255,255,255,.055); border: 1px solid rgba(255,255,255,.12);
  border-radius: 16px; padding: .85rem .9rem; backdrop-filter: blur(8px);
}
.pp-stat b { display: block; font-size: 1.2rem; letter-spacing: -.02em; color: #fff; }
.pp-stat span { display: block; color: rgba(236,253,245,.62); font-size: .72rem; margin-top: .15rem; }

.pp-cta-note { text-align: center; color: var(--pp-slate); font-size: .78rem; margin: .65rem 0 2.4rem; }
.pp-cta-note code { background: #e6fffb; color: #0f766e; border: 1px solid #b7ebe3; border-radius: 6px; padding: .1rem .35rem; }
.pp-section { margin: 2.6rem 0 .9rem; }
.pp-kicker { display: block; color: var(--pp-teal); font-size: .7rem; font-weight: 800; letter-spacing: .15em; text-transform: uppercase; margin-bottom: .45rem; }
.pp-section h2 { color: var(--pp-ink); font-size: clamp(1.55rem, 2.7vw, 2.35rem); letter-spacing: -.035em; margin: 0 0 .4rem; }
.pp-section p { color: var(--pp-slate); max-width: 700px; line-height: 1.6; margin: 0; }
.pp-features { display: grid; grid-template-columns: repeat(2, minmax(0,1fr)); gap: .85rem; margin-top: 1.2rem; }
.pp-card {
  background: rgba(255,255,255,.86); border: 1px solid var(--pp-line); border-radius: 20px;
  padding: 1.25rem 1.3rem; box-shadow: 0 10px 26px rgba(15,23,42,.04);
}
.pp-card h3 { color: var(--pp-ink); font-size: 1.02rem; margin: .55rem 0 .35rem; letter-spacing: -.015em; }
.pp-card p { color: var(--pp-slate); font-size: .9rem; line-height: 1.55; margin: 0; }
.pp-icon {
  width: 36px; height: 36px; border-radius: 11px; display: inline-flex; align-items: center;
  justify-content: center; background: var(--pp-mint); color: var(--pp-teal); font-weight: 850;
}
.pp-steps { display: grid; grid-template-columns: repeat(3, minmax(0,1fr)); gap: .85rem; margin-top: 1.2rem; }
.pp-step { background: #fff; border: 1px solid var(--pp-line); border-radius: 18px; padding: 1.15rem; }
.pp-step .n { color: var(--pp-teal); font-weight: 850; font-size: .78rem; letter-spacing: .1em; }
.pp-step h3 { color: var(--pp-ink); font-size: .98rem; margin: .4rem 0 .3rem; }
.pp-step p { color: var(--pp-slate); font-size: .86rem; line-height: 1.55; margin: 0; }
.pp-security {
  margin-top: 1.2rem; border-radius: 22px; padding: 1.4rem 1.5rem; color: #e6fffb;
  background: linear-gradient(135deg, #0b3b39, #0b1f2a);
  border: 1px solid rgba(255,255,255,.1);
}
.pp-security h3 { margin: 0 0 .5rem; font-size: 1.05rem; color: #fff; }
.pp-security ul { margin: .4rem 0 0; padding-left: 1.1rem; color: rgba(230,255,251,.78); line-height: 1.7; font-size: .9rem; }
.pp-footer { margin-top: 2.4rem; padding-top: 1.1rem; border-top: 1px solid var(--pp-line); color: #64748b; font-size: .76rem; display: flex; justify-content: space-between; gap: 1rem; flex-wrap: wrap; }

.pp-login-hero {
  border-radius: 26px; padding: 2rem; color: #ecfdf5;
  background:
    radial-gradient(380px 220px at 10% 0%, rgba(45,212,191,.28), transparent 70%),
    linear-gradient(145deg, #06302d, #0b1f2a);
  border: 1px solid rgba(255,255,255,.1); min-height: 100%;
}
.pp-login-hero h2 { color: #fff; font-size: clamp(1.6rem, 2.8vw, 2.3rem); letter-spacing: -.04em; margin: .8rem 0 .6rem; }
.pp-login-hero p { color: rgba(236,253,245,.74); line-height: 1.65; font-size: .94rem; }
.pp-login-hero ul { color: rgba(236,253,245,.78); line-height: 1.7; font-size: .86rem; padding-left: 1.1rem; }
.pp-login-badge { display: inline-flex; align-items: center; gap: .4rem; font-size: .68rem; font-weight: 800; letter-spacing: .14em; text-transform: uppercase; color: #99f6e4; }
.pp-login-badge:before { content: ""; width: 6px; height: 6px; border-radius: 50%; background: #5eead4; }

div[data-testid="stVerticalBlockBorderWrapper"] {
  background: rgba(255,255,255,.94); border: 1px solid var(--pp-line);
  border-radius: 22px; padding: 1.15rem 1.2rem; box-shadow: 0 12px 28px rgba(15,23,42,.045);
}
div[data-testid="stForm"] {
  background: linear-gradient(180deg, rgba(255,255,255,.98), rgba(248,252,251,.96));
  border: 1px solid var(--pp-line);
  border-radius: 22px; padding: 1.25rem; box-shadow: 0 16px 38px rgba(15,23,42,.06);
}
.stButton > button, .stFormSubmitButton > button,
[data-testid="stBaseButton-secondary"], [data-testid="stBaseButton-primary"] {
  border-radius: 13px; min-height: 2.9rem; padding: .55rem .95rem;
  font-weight: 750; letter-spacing: -.01em;
  transition: transform .16s ease, box-shadow .16s ease, background .16s ease, border-color .16s ease;
}
.stButton > button:hover, .stFormSubmitButton > button:hover,
[data-testid="stBaseButton-secondary"]:hover, [data-testid="stBaseButton-primary"]:hover {
  transform: translateY(-1px);
  box-shadow: 0 9px 20px rgba(15,23,42,.12);
}
.stButton > button:active, .stFormSubmitButton > button:active,
[data-testid="stBaseButton-secondary"]:active, [data-testid="stBaseButton-primary"]:active {
  transform: translateY(0);
}
.stButton > button:focus-visible, .stFormSubmitButton > button:focus-visible,
[data-testid="stBaseButton-secondary"]:focus-visible, [data-testid="stBaseButton-primary"]:focus-visible {
  outline: 3px solid rgba(13,148,136,.32); outline-offset: 2px;
}
.stButton > button[kind="primary"], .stFormSubmitButton > button[kind="primary"],
[data-testid="stBaseButton-primary"] {
  background: linear-gradient(135deg, #0f766e, #0d9488); border: 1px solid rgba(15,118,110,.25);
  color: #fff; box-shadow: 0 8px 20px rgba(15,118,110,.20);
}
.stButton > button[kind="primary"]:hover, .stFormSubmitButton > button[kind="primary"]:hover,
[data-testid="stBaseButton-primary"]:hover {
  background: linear-gradient(135deg, #0b5f59, #0f766e);
  box-shadow: 0 12px 26px rgba(15,118,110,.28);
}
.stButton > button[kind="secondary"], [data-testid="stBaseButton-secondary"] {
  background: #ffffff; color: #0b3b39; border: 1px solid #c7d9d6;
  box-shadow: 0 2px 7px rgba(15,23,42,.045);
}
.stButton > button[kind="secondary"]:hover, [data-testid="stBaseButton-secondary"]:hover {
  background: #f7fdfc; border-color: #8fc9c0;
}
div[data-baseweb="input"], div[data-baseweb="textarea"] {
  border-radius: 12px !important; border-color: #cbdad8 !important;
  background: #fbfdfd !important;
}
div[data-baseweb="input"]:focus-within, div[data-baseweb="textarea"]:focus-within {
  border-color: #0d9488 !important;
  box-shadow: 0 0 0 3px rgba(13,148,136,.12) !important;
}
[data-testid="stTextInput"] input, [data-testid="stTextArea"] textarea {
  color: #0f172a; font-size: .95rem;
}
[data-testid="stTextArea"] textarea { line-height: 1.55; }
[data-testid="stAlert"] { border-radius: 14px; border: 1px solid #d9e7e4; }
[data-testid="stExpander"] {
  border: 1px solid var(--pp-line); border-radius: 14px; background: rgba(255,255,255,.9);
  overflow: hidden;
}
[data-testid="stExpander"] summary { padding: .72rem .85rem; font-weight: 680; }
[data-testid="stExpander"] summary:hover { background: #f6fbfa; }
[data-testid="stTabs"] [role="tablist"] {
  gap: .3rem; border-bottom: 1px solid var(--pp-line); padding-bottom: .25rem;
}
[data-testid="stTabs"] [role="tab"] {
  border-radius: 11px 11px 0 0; padding: .6rem .9rem; font-weight: 720; color: #64748b;
}
[data-testid="stTabs"] [role="tab"]:hover { background: #f1faf8; color: #0f766e; }
[data-testid="stTabs"] [role="tab"][aria-selected="true"] {
  color: #0f766e; background: linear-gradient(180deg, #f0fdfa, #ffffff);
  box-shadow: inset 0 -2px 0 #0d9488;
}
[data-testid="stCode"] { border-radius: 12px; }
[data-testid="stSidebar"] {
  background: linear-gradient(180deg, #07302d, #0b1f2a); border-right: 1px solid rgba(255,255,255,.06);
}
[data-testid="stSidebar"] * { color: #e6fffb; }
[data-testid="stSidebar"] .stButton > button {
  background: rgba(255,255,255,.08); border: 1px solid rgba(255,255,255,.14); color: #e6fffb;
}
[data-testid="stSidebar"] .stButton > button:hover { background: rgba(255,255,255,.14); }
[data-testid="stSidebar"] [data-testid="stCaptionContainer"] * { color: rgba(230,255,251,.62); }
.pp-portal-band {
  border: 1px solid var(--pp-line); border-radius: 24px; padding: 1.35rem 1.5rem;
  background: linear-gradient(120deg, rgba(255,255,255,.95), rgba(240,253,250,.9));
  box-shadow: 0 12px 30px rgba(15,23,42,.04); margin-bottom: 1rem;
}
.pp-portal-band h1 { color: var(--pp-ink); font-size: clamp(1.55rem, 2.6vw, 2.15rem); letter-spacing: -.035em; margin: .25rem 0 .3rem; }
.pp-portal-band p { color: var(--pp-slate); margin: 0; line-height: 1.55; }
.pp-appbar {
  display: flex; align-items: center; justify-content: space-between; gap: 1rem;
  padding: .15rem 0 .95rem; color: var(--pp-ink);
}
.pp-appbar-brand { display: flex; align-items: center; gap: .6rem; font-weight: 800; letter-spacing: -.02em; }
.pp-appbar-context {
  display: inline-flex; align-items: center; gap: .4rem; padding: .34rem .6rem;
  border: 1px solid var(--pp-line); border-radius: 999px; background: rgba(255,255,255,.8);
  color: #475569; font-size: .7rem; font-weight: 750; letter-spacing: .04em; text-transform: uppercase;
}
.pp-appbar-context .pp-dot { background: #0d9488; }
.pp-chip-row { display: flex; flex-wrap: wrap; gap: .4rem; margin-top: .55rem; }
.pp-chip {
  display: inline-flex; align-items: center; padding: .3rem .55rem; border-radius: 999px;
  background: #e6fffb; color: #0f766e; border: 1px solid #b7ebe3; font-size: .72rem; font-weight: 720;
}
.pp-chip.pp-role { background: #eef2ff; color: #4338ca; border-color: #c7d2fe; }
.pp-chip.pp-muted { background: #f1f5f9; color: #64748b; border-color: #e2e8f0; }
.pp-table-wrap { overflow-x: auto; border: 1px solid var(--pp-line); border-radius: 16px; background: #fff; }
.pp-table { width: 100%; border-collapse: collapse; font-size: .82rem; }
.pp-table th { text-align: left; color: #475569; font-size: .68rem; letter-spacing: .08em; text-transform: uppercase; background: #f8fafc; padding: .65rem .8rem; border-bottom: 1px solid var(--pp-line); }
.pp-table td { padding: .65rem .8rem; border-bottom: 1px solid #eef2f6; color: #0f172a; vertical-align: top; }
.pp-table tr:last-child td { border-bottom: 0; }
.pp-on { color: #047857; font-weight: 750; }
.pp-off { color: #b91c1c; font-weight: 750; }
.pp-note { color: var(--pp-slate); font-size: .78rem; line-height: 1.5; }
.pp-stat-grid { display: grid; grid-template-columns: repeat(4, minmax(0,1fr)); gap: .7rem; margin-bottom: 1rem; }
.pp-stat-card { background: #fff; border: 1px solid var(--pp-line); border-radius: 16px; padding: .9rem 1rem; }
.pp-stat-card b { display: block; font-size: 1.35rem; letter-spacing: -.03em; color: var(--pp-ink); }
.pp-stat-card span { color: var(--pp-slate); font-size: .72rem; }
.pp-mono { font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace; font-size: .8rem; }

.pp-hero-grid { position: relative; display: grid; grid-template-columns: minmax(0, 1.18fr) minmax(300px, .82fr); gap: clamp(1.2rem, 3vw, 2.4rem); align-items: center; }
.pp-hero-copy h1 { margin-top: .95rem; }
.pp-product {
  position: relative; background: rgba(255,255,255,.97); color: #0f172a;
  border: 1px solid rgba(255,255,255,.55); border-radius: 22px; padding: 1rem;
  box-shadow: 0 24px 60px rgba(2,6,23,.32); transform: rotate(.6deg);
}
.pp-product-bar { display: flex; align-items: center; justify-content: space-between; gap: .5rem; color: #64748b; font-size: .68rem; font-weight: 750; letter-spacing: .08em; text-transform: uppercase; }
.pp-product-dot { width: 7px; height: 7px; border-radius: 50%; background: #10b981; display: inline-block; margin-right: .3rem; }
.pp-product-q { margin: .8rem 0 .55rem; font-size: .85rem; font-weight: 750; color: #0f766e; background: #f0fdfa; border: 1px solid #ccfbf1; border-radius: 12px; padding: .6rem .7rem; }
.pp-product-a { color: #0f172a; font-size: .93rem; line-height: 1.55; }
.pp-product-cite { margin-top: .7rem; border-left: 3px solid #14b8a6; padding: .45rem .65rem; background: #f8fafc; color: #475569; font-size: .72rem; line-height: 1.5; }
.pp-product-meta { display: flex; align-items: center; gap: .35rem; margin-top: .7rem; color: #0f766e; font-size: .68rem; font-weight: 750; }
.pp-product-lock { color: #b91c1c; }

.pp-role-grid { display: grid; grid-template-columns: repeat(3, minmax(0,1fr)); gap: .85rem; margin-top: 1.15rem; }
.pp-role-card { background: #fff; border: 1px solid var(--pp-line); border-radius: 20px; padding: 1.1rem; box-shadow: 0 10px 26px rgba(15,23,42,.04); }
.pp-role-card.pp-denied { background: linear-gradient(180deg, #fff, #fff7f7); border-color: #fecaca; }
.pp-role-card.pp-visible { background: linear-gradient(180deg, #fff, #f0fdfa); border-color: #b7ebe3; }
.pp-role-label { display: inline-flex; align-items: center; gap: .35rem; font-size: .68rem; font-weight: 800; letter-spacing: .1em; text-transform: uppercase; color: #0f766e; }
.pp-role-card.pp-denied .pp-role-label { color: #b91c1c; }
.pp-role-q { margin: .65rem 0; color: #0f172a; font-size: .9rem; font-weight: 720; line-height: 1.45; }
.pp-role-verdict { display: inline-flex; align-items: center; gap: .3rem; padding: .28rem .5rem; border-radius: 999px; background: #f1f5f9; color: #475569; font-size: .7rem; font-weight: 750; }
.pp-role-card.pp-denied .pp-role-verdict { background: #fee2e2; color: #991b1b; }
.pp-role-card.pp-visible .pp-role-verdict { background: #ccfbf1; color: #0f766e; }
.pp-role-card p { color: var(--pp-slate); font-size: .82rem; line-height: 1.55; margin: .65rem 0 0; }

.pp-demo {
  margin-top: 1.1rem; border: 1px solid #b7ebe3; border-radius: 24px; padding: 1.2rem 1.3rem;
  background:
    radial-gradient(520px 220px at 95% 0%, rgba(186,230,253,.45), transparent 68%),
    linear-gradient(120deg, #f0fdfa, #ffffff 62%, #f0f9ff);
  box-shadow: 0 14px 34px rgba(15,118,110,.08);
}
.pp-demo-head { display: flex; align-items: flex-start; justify-content: space-between; gap: 1rem; flex-wrap: wrap; }
.pp-demo h2 { color: var(--pp-ink); font-size: clamp(1.3rem, 2.2vw, 1.6rem); letter-spacing: -.03em; margin: .15rem 0 .3rem; }
.pp-demo p { color: var(--pp-slate); font-size: .88rem; line-height: 1.55; margin: 0; max-width: 760px; }
.pp-demo-grid { display: grid; grid-template-columns: repeat(5, minmax(0,1fr)); gap: .55rem; margin-top: .9rem; }
.pp-demo-card { background: transparent; border: 0; padding: 0; min-height: 100%; }
.pp-demo-card-top { display: flex; align-items: center; gap: .55rem; }
.pp-demo-icon {
  width: 38px; height: 38px; border-radius: 12px; display: inline-flex; align-items: center;
  justify-content: center; background: linear-gradient(135deg, #ccfbf1, #e0f2fe); color: #0f766e;
  border: 1px solid #b7ebe3; font-weight: 850; font-size: .95rem;
}
.pp-demo-card b { display: block; color: var(--pp-ink); font-size: .96rem; letter-spacing: -.015em; }
.pp-demo-card .pp-demo-desc { display: block; color: var(--pp-slate); font-size: .78rem; line-height: 1.5; margin-top: .55rem; min-height: 2.35rem; }
.pp-demo-meta { display: flex; align-items: center; gap: .35rem; margin-top: .55rem; color: #64748b; font-size: .7rem; font-weight: 650; }
.pp-demo-note { margin-top: .8rem; color: #64748b; font-size: .73rem; line-height: 1.5; }
.pp-demo-core { margin: .9rem 0 .35rem; color: #0f766e; font-size: .68rem; font-weight: 800; letter-spacing: .13em; text-transform: uppercase; }
.pp-demo-card button { margin-top: .65rem; }

.pp-answer-card { border: 1px solid #b7ebe3; border-radius: 20px; padding: 1.1rem 1.2rem; background: linear-gradient(180deg, #ffffff, #f0fdfa); }
.pp-answer-label { display: flex; align-items: center; justify-content: space-between; gap: .5rem; color: #0f766e; font-size: .68rem; font-weight: 800; letter-spacing: .12em; text-transform: uppercase; }
.pp-answer-text { margin: .55rem 0 .7rem; color: #0f172a; font-size: 1.02rem; line-height: 1.6; }
.pp-answer-meta { color: #64748b; font-size: .74rem; }
.pp-source-card { border: 1px solid var(--pp-line); border-radius: 16px; padding: .85rem .95rem; background: #fff; margin-bottom: .55rem; }
.pp-source-title { color: var(--pp-ink); font-weight: 750; font-size: .9rem; }
.pp-source-section { color: var(--pp-teal); font-size: .75rem; font-weight: 650; margin-top: .1rem; }
.pp-source-snippet { color: var(--pp-slate); font-size: .84rem; line-height: 1.55; margin-top: .45rem; }
.pp-source-meta { color: #94a3b8; font-size: .68rem; margin-top: .35rem; word-break: break-word; }

.pp-policy-grid { display: grid; grid-template-columns: repeat(2, minmax(0,1fr)); gap: .8rem; margin-top: .75rem; }
.pp-policy-card { border: 0; border-radius: 0; padding: 0; background: transparent; box-shadow: none; }
.pp-policy-card h3 { color: var(--pp-ink); font-size: .95rem; letter-spacing: -.015em; margin: .35rem 0 .3rem; }
.pp-policy-card .pp-meta { color: #64748b; font-size: .72rem; line-height: 1.5; }
.pp-policy-card .pp-sections { color: var(--pp-slate); font-size: .76rem; line-height: 1.55; margin-top: .45rem; }
.pp-policy-card .pp-sections span { display: inline-block; background: #f1f5f9; border: 1px solid #e2e8f0; border-radius: 999px; padding: .2rem .45rem; margin: .15rem .15rem 0 0; }
.pp-access-note { margin-top: .8rem; color: var(--pp-slate); font-size: .76rem; line-height: 1.5; }

.pp-matrix { width: 100%; border-collapse: collapse; font-size: .78rem; min-width: 760px; }
.pp-matrix th { text-align: center; color: #475569; font-size: .64rem; letter-spacing: .06em; text-transform: uppercase; background: #f8fafc; padding: .55rem .45rem; border-bottom: 1px solid var(--pp-line); }
.pp-matrix th:first-child, .pp-matrix td:first-child { text-align: left; padding-left: .7rem; }
.pp-matrix td { text-align: center; padding: .55rem .45rem; border-bottom: 1px solid #eef2f6; color: #0f172a; }
.pp-matrix tr:last-child td { border-bottom: 0; }
.pp-matrix .pp-yes { color: #047857; font-weight: 850; }
.pp-matrix .pp-no { color: #cbd5e1; }
.pp-matrix .pp-disabled { color: #b91c1c; font-size: .7rem; font-weight: 700; }

@media (max-width: 980px) {
  .pp-hero-grid { grid-template-columns: 1fr; }
  .pp-product { transform: none; }
  .pp-demo-grid { grid-template-columns: repeat(2, minmax(0,1fr)); }
}
@media (max-width: 820px) {
  .pp-stats, .pp-stat-grid { grid-template-columns: repeat(2, minmax(0,1fr)); }
  .pp-features, .pp-steps, .pp-role-grid, .pp-policy-grid { grid-template-columns: 1fr; }
  .pp-hero { border-radius: 22px; }
  .pp-nav-meta { display: none; }
  .pp-demo-grid { grid-template-columns: 1fr; }
}
</style>
"""

_NAV = """
<div class="pp-nav">
  <div class="pp-brand"><span class="pp-logo">P</span><span>PolicyPilot</span></div>
  <div class="pp-nav-meta"><span class="pp-dot"></span> Local demo · approved corpus</div>
</div>
"""

_HERO = """
<div class="pp-hero">
  <div class="pp-eyebrow"><span class="pp-pulse"></span> Permission-aware HR policy assistant</div>
  <div class="pp-hero-grid">
    <div class="pp-hero-copy">
      <h1>Policy answers, <span class="pp-gradient">scoped to your role.</span></h1>
      <p class="pp-lead">
        Ask an HR question in plain language. PolicyPilot searches only the policies
        your account can access, checks every passage again, and returns a cited answer —
        never restricted text.
      </p>
      <div class="pp-stats">
        <div class="pp-stat"><b>18</b><span>approved policies</span></div>
        <div class="pp-stat"><b>656</b><span>indexed passages</span></div>
        <div class="pp-stat"><b>5</b><span>access levels</span></div>
        <div class="pp-stat"><b>100%</b><span>answers cited</span></div>
      </div>
    </div>
    <div class="pp-product" aria-label="Product preview">
      <div class="pp-product-bar"><span><span class="pp-product-dot"></span>Employee workspace</span><span>Ask</span></div>
      <div class="pp-product-q">What standard of professionalism does the Code of Conduct expect?</div>
      <div class="pp-product-a">Employees are expected to maintain a high standard of professionalism in all interactions.</div>
      <div class="pp-product-cite"><strong>Code of Conduct &amp; Ethics</strong><br>Workplace Behavior Standards · Professional Conduct</div>
      <div class="pp-product-meta"><span>✓</span> Visible via Public + Employee · cited source</div>
    </div>
  </div>
</div>
"""

_ROLE_CONTRAST = """
<div class="pp-section">
  <span class="pp-kicker">The differentiator</span>
  <h2>One question. Three roles. Three different answers.</h2>
  <p>Permissions change what retrieval can see. The UI never decides this — the server does.</p>
</div>
<div class="pp-role-grid">
  <article class="pp-role-card pp-denied">
    <span class="pp-role-label">🔒 Employee</span>
    <div class="pp-role-q">“How is a grievance investigated?”</div>
    <span class="pp-role-verdict">No accessible policy</span>
    <p>The manager and HR procedures stay outside this account's retrieval scope. No restricted snippet is shown, even in the abstention.</p>
  </article>
  <article class="pp-role-card pp-visible">
    <span class="pp-role-label">Manager</span>
    <div class="pp-role-q">“How is a grievance investigated?”</div>
    <span class="pp-role-verdict">Manager procedure cited</span>
    <p>Manager responsibilities are retrieved because this account carries the Manager grant. The citation is re-authorized before it is returned.</p>
  </article>
  <article class="pp-role-card pp-visible">
    <span class="pp-role-label">HR</span>
    <div class="pp-role-q">“How is a grievance investigated?”</div>
    <span class="pp-role-verdict">HR case guidance cited</span>
    <p>HR-only handling detail becomes available to the HR account. The same question can produce a different, still-cited answer.</p>
  </article>
</div>
<p class="pp-note" style="margin-top:.7rem;">Illustrative role behavior. No restricted policy text is displayed on this public page.</p>
"""

_FEATURES = """
<div class="pp-section">
  <span class="pp-kicker">Built for employees</span>
  <h2>Less searching. More confidence.</h2>
  <p>A clear answer, the policy behind it, and a workspace that never shows what you cannot access.</p>
</div>
<div class="pp-features">
  <article class="pp-card"><span class="pp-icon">01</span><h3>Answers, then evidence</h3><p>Each response starts with a direct extract and links it to the exact policy section used. No invented policy.</p></article>
  <article class="pp-card"><span class="pp-icon">02</span><h3>Only your policies</h3><p>Retrieval is filtered by your current server-side grants before ranking. Unknown or malformed labels deny.</p></article>
  <article class="pp-card"><span class="pp-icon">03</span><h3>A library you can browse</h3><p>See the policy titles, sections, and update dates available to you — without opening a search box first.</p></article>
  <article class="pp-card"><span class="pp-icon">04</span><h3>Access you can inspect</h3><p>Every citation shows why it is visible. Admin and Evaluator roles never add restricted content.</p></article>
</div>
"""

_HOW = """
<div class="pp-section">
  <span class="pp-kicker">The flow</span>
  <h2>Ask. Authorize. Cite.</h2>
  <p>A deliberately narrow path from question to evidence.</p>
</div>
<div class="pp-steps">
  <div class="pp-step"><div class="n">STEP 01</div><h3>Ask in plain language</h3><p>Use the question you would ask HR. The workspace suggests common starting points.</p></div>
  <div class="pp-step"><div class="n">STEP 02</div><h3>Search inside your access</h3><p>Retrieval is filtered by the permissions resolved from the server store on every request.</p></div>
  <div class="pp-step"><div class="n">STEP 03</div><h3>Read the cited evidence</h3><p>Each candidate is independently authorized, re-read, and returned only if it still passes.</p></div>
</div>
"""

_SECURITY = """
<div class="pp-security">
  <h3>Trust is enforced, not promised.</h3>
  <ul>
    <li>Deny by default; unknown or malformed permissions are never normalized.</li>
    <li>Public + Employee is an authenticated baseline, not a document default.</li>
    <li>Admin and Evaluator are feature roles and never grant restricted content.</li>
    <li>Citations and source reads are re-authorized; there is no response cache.</li>
    <li>Local demo only — not production certified. The corpus is CC BY 4.0.</li>
  </ul>
</div>
<div class="pp-footer">
  <span>PolicyPilot · Permission-aware policy RAG</span>
  <span>CC BY 4.0 corpus · local demo · no response cache</span>
</div>
"""

_LOGIN_EMPLOYEE = """
<div class="pp-login-hero">
  <div class="pp-login-badge">Employee portal</div>
  <h2>Your policy workspace.</h2>
  <p>Ask the question you would take to HR. Get a cited answer and the exact policy section behind it.</p>
  <ul>
    <li>Browse the policy library available to your account</li>
    <li>See why each citation is visible to you</li>
    <li>Restricted passages stay outside retrieval and citations</li>
  </ul>
</div>
"""

_LOGIN_ADMIN = """
<div class="pp-login-hero">
  <div class="pp-login-badge">Administrator portal</div>
  <h2>See the access model at a glance.</h2>
  <p>Review accounts, effective content access, system roles, and corpus readiness without exposing policy text.</p>
  <ul>
    <li>Access matrix across Public, Employee, Manager, HR, and Finance</li>
    <li>Corpus document and passage counts, never password hashes</li>
    <li>Permission changes and ingestion remain trusted local CLI actions</li>
  </ul>
</div>
"""


_DEMO_USERNAME = re.compile(r"[A-Za-z0-9_.-]{1,64}", re.ASCII)


@dataclass(frozen=True)
class _DemoAccount:
    label: str
    description: str
    username: str
    password: str = field(repr=False)
    destination: str


def _printable(value: str, maximum: int) -> bool:
    return (
        0 < len(value) <= maximum
        and value.strip() == value
        and all(ord(character) >= 32 and ord(character) != 127 for character in value)
    )


def _load_demo_accounts() -> tuple[_DemoAccount, ...]:
    """Read a private, local-only demo credential file; malformed files hide the panel."""
    path = Path(os.getenv("POLICYPILOT_UI_DEMO_CREDENTIALS", ".cache/demo-accounts.json"))
    try:
        if path.is_symlink() or not path.is_file():
            return ()
        if path.stat().st_mode & 0o077:
            return ()
        descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        try:
            raw = os.read(descriptor, 16_385)
        finally:
            os.close(descriptor)
        if not 0 < len(raw) <= 16_384:
            return ()
        payload: object = json.loads(raw.decode("utf-8"))
        if type(payload) is not dict or payload.keys() != {"version", "password", "accounts"}:
            return ()
        password = payload["password"]
        accounts = payload["accounts"]
        if (
            type(payload["version"]) is not int
            or payload["version"] != 1
            or type(password) is not str
            or not 12 <= len(password.encode("utf-8")) <= 1024
            or type(accounts) is not list
            or not 1 <= len(accounts) <= 8
        ):
            return ()
        parsed: list[_DemoAccount] = []
        for item in accounts:
            if type(item) is not dict or item.keys() != {
                "label",
                "description",
                "username",
                "destination",
            }:
                return ()
            label, description = item["label"], item["description"]
            username, destination = item["username"], item["destination"]
            if (
                type(label) is not str
                or type(description) is not str
                or type(username) is not str
                or type(destination) is not str
                or not _printable(label, 40)
                or not _printable(description, 160)
                or _DEMO_USERNAME.fullmatch(username) is None
                or destination not in {"workspace", "admin"}
            ):
                return ()
            parsed.append(
                _DemoAccount(
                    label=label,
                    description=description,
                    username=username,
                    password=password,
                    destination=destination,
                )
            )
        if len({account.label for account in parsed}) != len(parsed) or len(
            {account.username for account in parsed}
        ) != len(parsed):
            return ()
        return tuple(parsed)
    except (OSError, UnicodeError, ValueError, TypeError):
        return ()


def _demo_sign_in(client: ApiClient, account: _DemoAccount) -> None:
    _clear_answer()
    st.session_state.pop("notice", None)
    st.session_state.pop("demo_persona", None)
    try:
        st.session_state["access_token"] = client.login(account.username, account.password)
    except DashboardError as error:
        st.session_state["notice"] = str(error)
        return
    st.session_state["auth_mode"] = "admin" if account.destination == "admin" else "employee"
    st.session_state["page"] = "admin" if account.destination == "admin" else "workspace"
    st.session_state["demo_persona"] = account.label


def _esc(value: object) -> str:
    return html.escape(str(value))


def _clear_answer() -> None:
    for key in ("query_answer", "source_view"):
        st.session_state.pop(key, None)


def _open_login(mode: str) -> None:
    st.session_state["auth_mode"] = mode
    st.session_state["page"] = "login"
    st.session_state.pop("notice", None)
    st.session_state.pop("login_password", None)
    st.session_state.pop("demo_persona", None)


def _back_home() -> None:
    st.session_state["page"] = "landing"
    st.session_state.pop("login_password", None)
    st.session_state.pop("notice", None)


def _sign_in(client: ApiClient) -> None:
    username: str = st.session_state["login_username"]
    password: str = st.session_state["login_password"]
    mode: str = st.session_state.get("auth_mode", "employee")
    st.session_state["login_password"] = ""
    st.session_state.pop("notice", None)
    st.session_state.pop("demo_persona", None)
    _clear_answer()
    try:
        st.session_state["access_token"] = client.login(username, password)
        st.session_state["page"] = "admin" if mode == "admin" else "workspace"
    except DashboardError as error:
        st.session_state["notice"] = str(error)


def _sign_out(client: ApiClient) -> None:
    token: str | None = st.session_state.pop("access_token", None)
    _clear_answer()
    st.session_state.pop("query_question", None)
    st.session_state.pop("demo_persona", None)
    st.session_state["page"] = "landing"
    st.session_state["auth_mode"] = "employee"
    if token is None:
        return
    try:
        client.logout(token)
        st.session_state["notice"] = "Signed out. Your server session was revoked."
    except DashboardError:
        st.session_state["notice"] = (
            "Signed out locally. Server revocation could not be confirmed; "
            "the server session may remain active until its absolute expiry."
        )


def _ask(client: ApiClient) -> None:
    token: str | None = st.session_state.get("access_token")
    question: str = st.session_state.get("query_question", "")
    _clear_answer()
    st.session_state.pop("notice", None)
    if token is None:
        return
    try:
        st.session_state["query_answer"] = client.query(token, question)
    except DashboardError as error:
        if error.kind == "unauthorized":
            st.session_state.pop("access_token", None)
            st.session_state["page"] = "landing"
            st.session_state["notice"] = "Your session ended. Please sign in again."
        else:
            st.session_state["notice"] = str(error)


def _read_source(client: ApiClient, token: str, citation: QueryCitation) -> None:
    try:
        st.session_state["source_view"] = client.source(token, citation.chunk_id)
    except DashboardError as error:
        if error.kind == "unauthorized":
            st.session_state.pop("access_token", None)
            st.session_state["page"] = "landing"
            st.session_state["notice"] = "Your session ended. Please sign in again."
        else:
            st.session_state["notice"] = str(error)


def _switch_employee() -> None:
    st.session_state["auth_mode"] = "employee"
    st.session_state["page"] = "workspace"
    _clear_answer()


def _switch_admin() -> None:
    st.session_state["auth_mode"] = "admin"
    st.session_state["page"] = "admin"
    _clear_answer()


def _notice() -> None:
    notice = st.session_state.pop("notice", None)
    if notice:
        st.info(notice)


def _demo_role_card(client: ApiClient, account: _DemoAccount) -> None:
    destination = "Admin console" if account.destination == "admin" else "Employee workspace"
    with st.container(border=True):
        st.markdown(
            f"""
            <div class="pp-demo-card">
              <div class="pp-demo-card-top">
                <span class="pp-demo-icon">{_esc(account.label[:1].upper())}</span>
                <b>{_esc(account.label)}</b>
              </div>
              <span class="pp-demo-desc">{_esc(account.description)}</span>
              <div class="pp-demo-meta"><span class="pp-dot"></span>{_esc(destination)}</div>
            </div>
            """,
            unsafe_allow_html=True,
        )
        st.button(
            f"Enter as {account.label} →",
            key=f"demo_login_{account.username}",
            type="primary" if account.label == "Employee" else "secondary",
            width="stretch",
            on_click=_demo_sign_in,
            args=(client, account),
        )


def _demo_panel(client: ApiClient, accounts: tuple[_DemoAccount, ...]) -> None:
    st.markdown(
        """
        <div class="pp-demo">
          <div class="pp-demo-head">
            <div>
              <span class="pp-kicker">Try the role difference</span>
              <h2>Choose a demo role.</h2>
              <p>Three core roles, plus Finance and Administrator for the wider access model. Each button signs in through the normal API — the UI never toggles grants.</p>
            </div>
          </div>
        </div>
        """,
        unsafe_allow_html=True,
    )
    for start, heading in ((0, "Core roles"), (3, "Extended roles")):
        group = accounts[start : start + 3]
        if not group:
            continue
        st.markdown(f'<div class="pp-demo-core">{heading}</div>', unsafe_allow_html=True)
        columns = st.columns(len(group), gap="medium")
        for column, account in zip(columns, group, strict=False):
            with column:
                _demo_role_card(client, account)
    st.markdown(
        '<div class="pp-demo-note">No password entry needed. Demo credentials are generated locally and stored in an ignored, owner-only file. Never reuse them with real employee data.</div>',
        unsafe_allow_html=True,
    )


def _account_entry_buttons(primary_employee: bool) -> None:
    employee, admin = st.columns(2, gap="medium")
    with employee:
        st.button(
            "Sign in as employee",
            key="landing_employee",
            type="primary" if primary_employee else "secondary",
            width="stretch",
            on_click=_open_login,
            args=("employee",),
        )
    with admin:
        st.button(
            "Administrator portal",
            key="landing_admin",
            type="secondary",
            width="stretch",
            on_click=_open_login,
            args=("admin",),
        )


def _landing(client: ApiClient) -> None:
    st.markdown(_NAV, unsafe_allow_html=True)
    _notice()
    st.markdown(_HERO, unsafe_allow_html=True)
    demo_accounts = _load_demo_accounts()
    if demo_accounts:
        _demo_panel(client, demo_accounts)
        with st.expander("Use an existing local account"):
            st.caption("For accounts provisioned with create-user or changed with set-access.")
            _account_entry_buttons(primary_employee=False)
        st.markdown(
            '<div class="pp-cta-note">Demo mode is active. These are synthetic local accounts, not production credentials.</div>',
            unsafe_allow_html=True,
        )
    else:
        _account_entry_buttons(primary_employee=True)
        st.markdown(
            '<div class="pp-cta-note">No default accounts. Use the credentials provisioned by your local operator, or run <code>make demo-seed</code> for the role switcher.</div>',
            unsafe_allow_html=True,
        )
    st.markdown(_ROLE_CONTRAST, unsafe_allow_html=True)
    st.markdown(_FEATURES, unsafe_allow_html=True)
    st.markdown(_HOW, unsafe_allow_html=True)
    st.markdown(_SECURITY, unsafe_allow_html=True)


def _login_page(client: ApiClient) -> None:
    mode = st.session_state.get("auth_mode", "employee")
    admin = mode == "admin"
    st.markdown(_NAV, unsafe_allow_html=True)
    if st.button("← Back to home", key="back_home", on_click=_back_home):
        return
    notice = st.session_state.pop("notice", None)
    if notice:
        st.error(notice)
    left, right = st.columns([1.08, 0.92], gap="large")
    with left:
        st.markdown(_LOGIN_ADMIN if admin else _LOGIN_EMPLOYEE, unsafe_allow_html=True)
    with right:
        with st.form("login"):
            st.markdown(
                f'<div class="pp-kicker">{"ADMINISTRATOR SIGN IN" if admin else "EMPLOYEE SIGN IN"}</div>',
                unsafe_allow_html=True,
            )
            st.text_input(
                "Username",
                key="login_username",
                max_chars=64,
                autocomplete="username",
            )
            st.text_input(
                "Password",
                type="password",
                key="login_password",
                max_chars=1024,
                autocomplete="current-password",
            )
            st.form_submit_button(
                "Sign in to administrator console" if admin else "Sign in to workspace",
                key="sign_in",
                type="primary",
                width="stretch",
                on_click=_sign_in,
                args=(client,),
            )
        st.caption(
            "Admin access is verified server-side. The Admin role never grants restricted content access."
            if admin
            else "Passwords and session tokens are never displayed. Sign out before closing the tab."
        )


def _portal_sidebar(client: ApiClient, identity: MeResponse, admin: bool) -> None:
    with st.sidebar:
        st.markdown(
            '<div class="pp-brand"><span class="pp-logo">P</span><span>PolicyPilot</span></div>',
            unsafe_allow_html=True,
        )
        st.caption("ADMINISTRATOR CONSOLE" if admin else "EMPLOYEE WORKSPACE")
        st.divider()
        if admin:
            st.button(
                "Employee workspace", key="nav_employee", width="stretch", on_click=_switch_employee
            )
        elif SystemRole.ADMIN in identity.system_roles:
            st.button(
                "Administrator console", key="nav_admin", width="stretch", on_click=_switch_admin
            )
        st.button(
            "Refresh permissions",
            key="refresh_permissions",
            width="stretch",
            on_click=_clear_answer,
        )
        if st.session_state.get("demo_persona"):
            st.button(
                "Switch demo role",
                key="switch_demo",
                width="stretch",
                on_click=_sign_out,
                args=(client,),
            )
        st.button("Sign out", key="sign_out", width="stretch", on_click=_sign_out, args=(client,))
        st.divider()
        if client.health():
            st.success("API reachable")
        else:
            st.warning("API unreachable")
        st.caption("Liveness only, not database/corpus/model readiness. No response cache.")


def _appbar(context: str, identity: MeResponse) -> None:
    persona = st.session_state.get("demo_persona")
    meta = f"{context} · {persona}" if type(persona) is str and persona else context
    st.markdown(
        f"""
        <div class="pp-appbar">
          <div class="pp-appbar-brand"><span class="pp-logo">P</span><span>PolicyPilot</span></div>
          <div class="pp-appbar-context"><span class="pp-dot"></span>{_esc(meta)}</div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def _portal_band(identity: MeResponse, admin: bool) -> None:
    title = "Administration" if admin else "Your policy workspace."
    subtitle = (
        "Read-only account and corpus overview. Content grants remain separate from the Admin role."
        if admin
        else (
            f"Signed in as <strong>{_esc(identity.user_id)}</strong>. Ask a policy question, "
            "inspect the citations, and review the access attached to your account."
        )
    )
    st.markdown(
        f"""
        <div class="pp-portal-band">
          <span class="pp-kicker">{"ADMINISTRATOR CONSOLE" if admin else "EMPLOYEE WORKSPACE"}</span>
          <h1>{title}</h1>
          <p>{subtitle}</p>
        </div>
        """,
        unsafe_allow_html=True,
    )


def _chips(values: Sequence[object], *, role: bool = False, empty: str = "Baseline only") -> str:
    if not values:
        return f'<span class="pp-chip pp-muted">{_esc(empty)}</span>'
    css = "pp-chip pp-role" if role else "pp-chip"
    return "".join(f'<span class="{css}">{_esc(value)}</span>' for value in values)


def _identity_card(identity: MeResponse) -> None:
    with st.container(border=True):
        st.markdown('<div class="pp-kicker">YOUR ACCESS</div>', unsafe_allow_html=True)
        st.markdown(f"**{_esc(identity.user_id)}**")
        st.caption(f"Permission version {identity.permission_version}")
        st.markdown("**Content permissions**")
        st.markdown(
            f'<div class="pp-chip-row">{_chips(identity.content_permissions)}</div>',
            unsafe_allow_html=True,
        )
        st.markdown("**System roles**")
        st.markdown(
            f'<div class="pp-chip-row">{_chips(identity.system_roles, role=True, empty="No system roles")}</div>',
            unsafe_allow_html=True,
        )
        st.caption("System roles control features. They never grant restricted content access.")


_SAMPLE_QUESTIONS = (
    "How much leave do full-time staff get?",
    "Can I request flexible work?",
    "How do I report harassment?",
    "What are the travel expense limits?",
)


def _set_prompt(question: str) -> None:
    st.session_state["query_question"] = question
    _clear_answer()
    st.session_state.pop("notice", None)


def _query_panel(client: ApiClient, token: str, identity: MeResponse) -> None:
    with st.container(border=True):
        st.markdown('<div class="pp-kicker">ASK POLICYPLOT</div>', unsafe_allow_html=True)
        st.subheader("What do you need to know?")
        st.caption(
            "Answers use only documents available to your account. Permissions are checked again "
            "for every question and every cited source."
        )
        st.markdown("**Try a question**")
        first, second = st.columns(2, gap="small")
        for index, question in enumerate(_SAMPLE_QUESTIONS):
            with first if index % 2 == 0 else second:
                st.button(
                    question,
                    key=f"sample_{index}",
                    width="stretch",
                    on_click=_set_prompt,
                    args=(question,),
                )
        st.caption(
            "Tip: include your employment type or role when it matters, such as full-time or manager."
        )
        with st.form("query"):
            st.text_area(
                "Your policy question",
                key="query_question",
                max_chars=2000,
                height=110,
                placeholder="For example: How much annual leave do full-time employees get?",
            )
            st.form_submit_button(
                "Ask question",
                key="ask_question",
                type="primary",
                width="stretch",
                on_click=_ask,
                args=(client,),
            )
        if notice := st.session_state.get("notice"):
            st.warning(notice)
            st.session_state.pop("notice", None)
        answer: QueryAnswer | None = st.session_state.get("query_answer")
        if answer is None:
            return
        if answer.status == "abstained":
            st.info(answer.answer)
            st.caption(
                "No restricted passage is shown or implied. Try a broader question, browse the "
                "policy library, or contact HR."
            )
            return
        st.markdown(
            f"""
            <div class="pp-answer-card">
              <div class="pp-answer-label"><span>Answer</span><span>{len(answer.citations)} cited source{"s" if len(answer.citations) != 1 else ""}</span></div>
              <div class="pp-answer-text">{_esc(answer.answer)}</div>
              <div class="pp-answer-meta">Extractive answer from authorized policy text. Every citation below was re-read and authorized.</div>
            </div>
            """,
            unsafe_allow_html=True,
        )
        st.markdown("**Authorized sources**")
        source_view = st.session_state.get("source_view")
        for index, citation in enumerate(answer.citations, start=1):
            with st.expander(f"[{index}] {citation.title} · {citation.section}"):
                st.markdown(
                    f"""
                    <div class="pp-source-card">
                      <div class="pp-source-title">{_esc(citation.title)}</div>
                      <div class="pp-source-section">{_esc(citation.section)}</div>
                      <div class="pp-source-snippet">{_esc(citation.snippet)}</div>
                      <div class="pp-source-meta">{_esc(citation.source_url)}</div>
                    </div>
                    """,
                    unsafe_allow_html=True,
                )
                st.button(
                    "View authorized source",
                    key=f"source_{index}",
                    on_click=_read_source,
                    args=(client, token, citation),
                )
                if source_view is not None and source_view.chunk_id == citation.chunk_id:
                    st.text(source_view.text)
        with st.expander("Why can I see these sources?"):
            st.markdown(
                f'<div class="pp-chip-row">{_chips(identity.content_permissions)}</div>',
                unsafe_allow_html=True,
            )
            st.caption(
                "These are the effective content permissions reported by the server for this "
                "session. Roles such as Admin do not add restricted content."
            )


def _policy_library(client: ApiClient, token: str) -> None:
    try:
        policies = client.policies(token)
    except DashboardError as error:
        if error.kind == "unauthorized":
            st.session_state.pop("access_token", None)
            st.session_state["page"] = "landing"
            st.session_state["notice"] = "Your session ended. Please sign in again."
            st.rerun()
        st.warning(str(error))
        st.caption("The policy library is unavailable. No policy data is cached or inferred.")
        return
    st.markdown(
        """
        <div class="pp-section" style="margin-top:.3rem;">
          <span class="pp-kicker">YOUR POLICY LIBRARY</span>
          <h2>Browse what you can access.</h2>
          <p>Titles, sections, and update dates are permission-filtered by the API. Passage text is not listed here.</p>
        </div>
        """,
        unsafe_allow_html=True,
    )
    if not policies:
        st.info("No policy documents are currently available to your account.")
        return
    columns = st.columns(2, gap="medium")
    for index, policy in enumerate(policies):
        with columns[index % 2], st.container(border=True):
            visible = " · ".join(policy.visible_via)
            st.markdown(
                f"""
                <div class="pp-policy-card">
                  <span class="pp-kicker">Policy</span>
                  <h3>{_esc(policy.title)}</h3>
                  <div class="pp-meta">{policy.chunks} indexed passages · updated {policy.updated_at:%d %b %Y}<br>Visible via {_esc(visible)}</div>
                  <div class="pp-sections">{"".join(f"<span>{_esc(section)}</span>" for section in policy.sections[:8])}</div>
                </div>
                """,
                unsafe_allow_html=True,
            )
            if len(policy.sections) > 8:
                st.caption(f"+ {len(policy.sections) - 8} more sections")
            with st.expander("View section labels"):
                for section in policy.sections:
                    st.caption(section)
            st.caption(policy.source_url)


def _degraded_portal(client: ApiClient, admin: bool) -> None:
    with st.sidebar:
        st.markdown(
            '<div class="pp-brand"><span class="pp-logo">P</span><span>PolicyPilot</span></div>',
            unsafe_allow_html=True,
        )
        st.caption("ADMINISTRATOR CONSOLE" if admin else "EMPLOYEE WORKSPACE")
        st.divider()
        st.button("Sign out", key="sign_out", width="stretch", on_click=_sign_out, args=(client,))
    st.error("Access could not be confirmed. Retry or sign out.")
    st.caption("The server did not return a current identity. No cached grants are displayed.")
    if st.button("Retry", key="retry_identity", type="primary"):
        st.rerun()


def _load_identity(client: ApiClient, token: str) -> MeResponse | None:
    try:
        return client.me(token)
    except DashboardError as error:
        if error.kind == "unauthorized":
            st.session_state.pop("access_token", None)
            st.session_state["page"] = "landing"
            st.session_state["notice"] = "Your session ended. Please sign in again."
            st.rerun()
        return None


def _employee_workspace(client: ApiClient, token: str) -> None:
    identity = _load_identity(client, token)
    if identity is None:
        _degraded_portal(client, admin=False)
        return
    _portal_sidebar(client, identity, admin=False)
    _appbar("Employee workspace", identity)
    _portal_band(identity, admin=False)
    ask_tab, library_tab = st.tabs(["Ask a question", "Policy library"])
    with ask_tab:
        main, aside = st.columns([1.72, 1.0], gap="large")
        with main:
            _query_panel(client, token, identity)
        with aside:
            _identity_card(identity)
            with st.container(border=True):
                st.markdown('<div class="pp-kicker">HOW THIS WORKS</div>', unsafe_allow_html=True)
                st.markdown(
                    "1. Your identity is resolved from the server store.\n"
                    "2. Retrieval is filtered by your effective permissions.\n"
                    "3. Every candidate is independently authorized.\n"
                    "4. Citations are re-read and re-authorized."
                )
                st.caption(
                    "Empty evidence returns one fixed abstention message. No answer is invented."
                )
    with library_tab:
        _policy_library(client, token)


def _accounts_table(overview: AdminOverview) -> str:
    rows = []
    for account in overview.accounts:
        permissions = " · ".join(account.permissions) or "—"
        roles = " · ".join(account.system_roles) or "—"
        state = (
            '<span class="pp-on">Enabled</span>'
            if account.enabled
            else '<span class="pp-off">Disabled</span>'
        )
        rows.append(
            f"<tr><td><strong>{_esc(account.username)}</strong><br>"
            f"<span class='pp-note'>{_esc(account.user_id)}</span></td>"
            f"<td>{state}</td><td>{_esc(permissions)}</td><td>{_esc(roles)}</td>"
            f"<td>{account.permission_version}</td></tr>"
        )
    return (
        '<div class="pp-table-wrap"><table class="pp-table"><thead><tr>'
        "<th>Account</th><th>State</th><th>Explicit content grants</th>"
        "<th>System roles</th><th>Version</th>"
        "</tr></thead><tbody>" + "".join(rows) + "</tbody></table></div>"
    )


def _access_matrix(overview: AdminOverview) -> str:
    content_columns = ("Public", "Employee", "Manager", "HR", "Finance")
    role_columns = ("Admin", "Evaluator")
    header = "".join(f"<th>{column}</th>" for column in (*content_columns, *role_columns))
    rows = []
    for account in overview.accounts:
        effective = (
            {Permission.PUBLIC.value, Permission.EMPLOYEE.value} | set(account.permissions)
            if account.enabled
            else set()
        )
        state = "" if account.enabled else ' <span class="pp-disabled">disabled</span>'
        content_cells = "".join(
            '<td class="pp-yes">✓</td>' if column in effective else '<td class="pp-no">·</td>'
            for column in content_columns
        )
        role_cells = "".join(
            '<td class="pp-yes">✓</td>'
            if column in account.system_roles
            else '<td class="pp-no">·</td>'
            for column in role_columns
        )
        rows.append(
            f"<tr><td><strong>{_esc(account.username)}</strong>{state}<br>"
            f"<span class='pp-note'>{_esc(account.user_id)}</span></td>"
            f"{content_cells}{role_cells}</tr>"
        )
    return (
        '<div class="pp-table-wrap"><table class="pp-matrix"><thead><tr>'
        f"<th>Account</th>{header}</tr></thead><tbody>" + "".join(rows) + "</tbody></table></div>"
        '<p class="pp-access-note">Every enabled authenticated account receives the Public + '
        "Employee baseline. Explicit grants are added by union. System roles unlock features "
        "only and never add content.</p>"
    )


def _admin_denied(client: ApiClient, identity: MeResponse) -> None:
    _portal_sidebar(client, identity, admin=True)
    _appbar("Administrator console", identity)
    _portal_band(identity, admin=True)
    with st.container(border=True):
        st.subheader("Administrator access required")
        st.write(
            "This account is signed in, but it does not hold the **Admin** system role. "
            "The Admin role is a feature permission and never grants restricted content."
        )
        first, second = st.columns(2)
        with first:
            st.button(
                "Open employee workspace",
                key="denied_employee",
                type="primary",
                width="stretch",
                on_click=_switch_employee,
            )
        with second:
            st.button(
                "Sign out",
                key="denied_sign_out",
                width="stretch",
                on_click=_sign_out,
                args=(client,),
            )
        st.caption("Ask a local operator to grant the Admin role with the trusted CLI.")


def _admin_portal(client: ApiClient, token: str) -> None:
    identity = _load_identity(client, token)
    if identity is None:
        _degraded_portal(client, admin=True)
        return
    if SystemRole.ADMIN not in identity.system_roles:
        _admin_denied(client, identity)
        return
    try:
        overview = client.admin_overview(token)
    except DashboardError as error:
        if error.kind in {"unauthorized", "forbidden"}:
            st.session_state.pop("access_token", None)
            st.session_state["page"] = "landing"
            st.session_state["notice"] = "Administrator access could not be confirmed."
            st.rerun()
        st.error(str(error))
        st.caption("The administration overview is unavailable. No account data is shown.")
        return
    _portal_sidebar(client, identity, admin=True)
    _appbar("Administrator console", identity)
    _portal_band(identity, admin=True)
    enabled_accounts = sum(account.enabled for account in overview.accounts)
    admin_accounts = sum("Admin" in account.system_roles for account in overview.accounts)
    st.markdown(
        f"""
        <div class="pp-stat-grid">
          <div class="pp-stat-card"><b>{enabled_accounts}</b><span>enabled accounts</span></div>
          <div class="pp-stat-card"><b>{admin_accounts}</b><span>administrators</span></div>
          <div class="pp-stat-card"><b>{overview.documents}</b><span>policy documents</span></div>
          <div class="pp-stat-card"><b>{overview.chunks}</b><span>indexed passages</span></div>
        </div>
        """,
        unsafe_allow_html=True,
    )
    with st.container(border=True):
        st.markdown('<div class="pp-kicker">ACCESS MATRIX</div>', unsafe_allow_html=True)
        st.subheader("Effective access at a glance")
        st.markdown(_access_matrix(overview), unsafe_allow_html=True)
    main, aside = st.columns([1.7, 1.0], gap="large")
    with main:
        with st.container(border=True):
            st.markdown('<div class="pp-kicker">ACCOUNT DIRECTORY</div>', unsafe_allow_html=True)
            st.subheader("People with local access")
            st.markdown(_accounts_table(overview), unsafe_allow_html=True)
            st.caption(
                "All active accounts also receive the Public + Employee authenticated baseline. "
                "Password hashes are never returned by the API."
            )
        with st.container(border=True):
            st.markdown('<div class="pp-kicker">OPERATOR CONTROLS</div>', unsafe_allow_html=True)
            st.subheader("Trusted local actions")
            st.write(
                "Permission changes and ingestion are intentionally **not** exposed as public "
                "write routes in this milestone. Use the trusted CLI:"
            )
            st.code(
                "uv run --frozen python -m policypilot.manage set-access <user> --permission HR\n"
                "uv run --frozen python -m policypilot.manage disable-user <user>\n"
                "uv run --frozen python -m policypilot.manage ingest-corpus \\\n"
                "  --manifest docs/corpus/english-policy-manifest.json --archive data/archive.zip",
                language="bash",
            )
            st.caption(
                "Every overview request resolves the current identity and Admin role again. "
                "No account or corpus data is cached."
            )
    with aside:
        with st.container(border=True):
            st.markdown('<div class="pp-kicker">ADMIN ACCOUNT</div>', unsafe_allow_html=True)
            st.markdown(f"**{_esc(identity.user_id)}**")
            st.markdown(
                f'<div class="pp-chip-row">{_chips(identity.system_roles, role=True)}</div>',
                unsafe_allow_html=True,
            )
            st.caption(
                "The Admin role unlocks this read-only console. It does not add restricted "
                "content permissions."
            )
        with st.container(border=True):
            st.markdown('<div class="pp-kicker">ACCESS COVERAGE</div>', unsafe_allow_html=True)
            hr_accounts = sum(
                Permission.HR.value in account.permissions for account in overview.accounts
            )
            finance_accounts = sum(
                Permission.FINANCE.value in account.permissions for account in overview.accounts
            )
            st.markdown(
                f"**{hr_accounts}** with explicit HR access  \n"
                f"**{finance_accounts}** with explicit Finance access  \n"
                f"**{enabled_accounts}** enabled of {len(overview.accounts)} total"
            )
            st.caption(
                "Counts include explicit grants only. Every enabled account also carries the "
                "Public + Employee baseline."
            )
        with st.container(border=True):
            st.markdown('<div class="pp-kicker">SECURITY POSTURE</div>', unsafe_allow_html=True)
            st.markdown(
                "- Admin overview is read-only\n"
                "- No password hashes returned\n"
                "- No restricted text in the response\n"
                "- No response cache\n"
                "- Permission changes remain CLI-only"
            )
        st.button(
            "Open employee workspace",
            key="admin_employee",
            width="stretch",
            on_click=_switch_employee,
        )


def render() -> None:
    st.set_page_config(
        page_title="PolicyPilot · Policy answers, scoped to your role",
        page_icon="🛡️",
        layout="wide",
        initial_sidebar_state="collapsed",
    )
    st.markdown(_CSS, unsafe_allow_html=True)
    page = st.session_state.setdefault("page", "landing")
    token: str | None = st.session_state.get("access_token")
    if page in {"workspace", "admin"} and token is None:
        page = "landing"
        st.session_state["page"] = "landing"
    try:
        client = ApiClient(os.getenv("POLICYPILOT_UI_API_BASE_URL", "http://127.0.0.1:8000"))
    except DashboardError:
        st.error("Invalid dashboard API configuration. Ask your local operator to check it.")
        return
    if page == "landing":
        _landing(client)
    elif page == "login":
        _login_page(client)
    elif page == "admin":
        _admin_portal(client, token or "")
    else:
        _employee_workspace(client, token or "")
