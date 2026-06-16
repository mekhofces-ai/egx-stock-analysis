from __future__ import annotations

import hashlib
import inspect
import re
from pathlib import Path
from typing import Any

import pandas as pd
import streamlit as st


SIGNAL_COLORS = {
    "STRONG BUY": "#15803d",
    "BUY": "#22c55e",
    "CONDITIONAL BUY": "#16a34a",
    "WATCH": "#2563eb",
    "WATCH ONLY": "#2563eb",
    "WAIT FOR PULLBACK": "#0f766e",
    "WATCH_ONLY_MARKET_BLOCKED": "#0f766e",
    "STALE_DATA": "#64748b",
    "HOLD": "#64748b",
    "NEUTRAL": "#64748b",
    "AVOID": "#f97316",
    "AVOID / SELL": "#dc2626",
    "SELL": "#dc2626",
}

RISK_COLORS = {"LOW": "#16a34a", "MEDIUM": "#f97316", "HIGH": "#dc2626"}
_ORIGINAL_DATAFRAME = None
_DATAFRAME_PATCH_INSTALLED = False


def inject_professional_css(theme: str = "light") -> None:
    dark = str(theme).lower() == "dark"
    bg = "#0f172a" if dark else "#f5f7fb"
    panel = "#111827" if dark else "#ffffff"
    panel_2 = "#1f2937" if dark else "#f8fafc"
    ink = "#e5e7eb" if dark else "#172033"
    muted = "#94a3b8" if dark else "#64748b"
    line = "#334155" if dark else "#dfe4ec"
    sidebar = "#020617" if dark else "#111827"
    st.markdown(
        f"""
        <style>
        :root {{
            --egx-bg: {bg};
            --egx-panel: {panel};
            --egx-panel-2: {panel_2};
            --egx-ink: {ink};
            --egx-muted: {muted};
            --egx-line: {line};
            --egx-blue: #2563eb;
            --egx-green: #16a34a;
            --egx-orange: #f97316;
            --egx-red: #dc2626;
        }}
        .stApp {{ background: var(--egx-bg); color: var(--egx-ink); }}
        .block-container {{ padding-top: 1.1rem; padding-bottom: 2rem; max-width: 1500px; }}
        [data-testid="stSidebar"] {{ background: {sidebar}; min-width: 248px !important; }}
        [data-testid="stSidebar"] * {{ color: #e5e7eb; }}
        [data-testid="stSidebar"] [role="radiogroup"] label {{
            border-radius: 8px; padding: 7px 9px; margin: 2px 0;
        }}
        h1, h2, h3, h4 {{ color: var(--egx-ink); letter-spacing: 0; }}
        h1 {{ font-size: 32px; line-height: 1.1; margin-bottom: .45rem; }}
        h2 {{ font-size: 23px; }}
        .egx-topbar {{
            display: flex; flex-wrap: wrap; gap: 10px; align-items: stretch;
            margin: 0 0 14px 0;
        }}
        .egx-chip {{
            background: var(--egx-panel); border: 1px solid var(--egx-line); border-radius: 8px;
            padding: 9px 12px; color: var(--egx-ink); box-shadow: 0 1px 2px rgba(15,23,42,.06);
        }}
        .egx-card {{
            background: var(--egx-panel); border: 1px solid var(--egx-line); border-radius: 8px;
            padding: 15px 16px; box-shadow: 0 8px 20px rgba(15,23,42,.06);
        }}
        .egx-card-label {{ color: var(--egx-muted); font-size: 12px; text-transform: uppercase; font-weight: 700; }}
        .egx-card-value {{ color: var(--egx-ink); font-size: 24px; font-weight: 800; margin-top: 2px; }}
        .egx-card-delta {{ color: var(--egx-muted); font-size: 13px; margin-top: 4px; }}
        .egx-section-title {{ font-size: 18px; font-weight: 800; margin: 12px 0 8px; color: var(--egx-ink); }}
        .egx-box {{
            border-radius: 8px; padding: 12px 14px; border: 1px solid var(--egx-line);
            background: var(--egx-panel-2); margin: 8px 0 12px;
        }}
        .egx-warning {{ background: #fff7ed; border-color: #fed7aa; color: #7c2d12; }}
        .egx-success {{ background: #ecfdf3; border-color: #bbf7d0; color: #14532d; }}
        .egx-empty {{ background: var(--egx-panel); border: 1px dashed var(--egx-line); color: var(--egx-muted); }}
        .egx-badge {{
            display: inline-flex; align-items: center; gap: 4px; border-radius: 999px;
            padding: 3px 10px; font-size: 12px; color: white; font-weight: 800;
        }}
        div[data-testid="stMetric"] {{
            background: var(--egx-panel); border: 1px solid var(--egx-line); border-radius: 8px;
            padding: 12px 14px; box-shadow: 0 4px 14px rgba(15,23,42,.05);
        }}
        div.stButton > button, div.stDownloadButton > button {{
            border-radius: 8px; border: 1px solid #1d4ed8; background: #2563eb; color: white; font-weight: 700;
        }}
        div.stButton > button:hover, div.stDownloadButton > button:hover {{
            border-color: #0f766e; color: white;
        }}
        </style>
        """,
        unsafe_allow_html=True,
    )


def signal_badge(signal: str | None) -> str:
    text = str(signal or "-").upper()
    color = SIGNAL_COLORS.get(text, "#64748b")
    return f'<span class="egx-badge" style="background:{color}">{text}</span>'


def risk_badge(risk: str | None) -> str:
    text = str(risk or "-").upper()
    color = RISK_COLORS.get(text, "#64748b")
    return f'<span class="egx-badge" style="background:{color}">{text}</span>'


def score_badge(score: float | None) -> str:
    value = float(score or 0)
    color = "#16a34a" if value >= 75 else "#2563eb" if value >= 60 else "#f97316" if value >= 40 else "#dc2626"
    return f'<span class="egx-badge" style="background:{color}">{value:.0f}%</span>'


def metric_card(label: str, value: Any, delta: Any | None = None) -> None:
    st.markdown(
        f"""
        <div class="egx-card">
            <div class="egx-card-label">{label}</div>
            <div class="egx-card-value">{value}</div>
            <div class="egx-card-delta">{delta or ""}</div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def section_title(title: str) -> None:
    st.markdown(f'<div class="egx-section-title">{title}</div>', unsafe_allow_html=True)


def empty_state(text: str) -> None:
    st.markdown(f'<div class="egx-box egx-empty">{text}</div>', unsafe_allow_html=True)


def warning_box(text: str) -> None:
    st.markdown(f'<div class="egx-box egx-warning">{text}</div>', unsafe_allow_html=True)


def success_box(text: str) -> None:
    st.markdown(f'<div class="egx-box egx-success">{text}</div>', unsafe_allow_html=True)


def _table_key(df: pd.DataFrame, prefix: str = "egx_table_search") -> str:
    this_file = Path(__file__).resolve()
    location = "unknown"
    for frame_info in inspect.stack()[1:10]:
        try:
            if Path(frame_info.filename).resolve() != this_file:
                location = f"{frame_info.filename}:{frame_info.lineno}"
                break
        except Exception:
            continue
    columns = "|".join(str(col) for col in list(df.columns)[:25])
    digest = hashlib.sha1(f"{location}:{columns}:{len(df)}".encode("utf-8", errors="ignore")).hexdigest()[:12]
    base = f"{prefix}_{digest}"
    seen = st.session_state.setdefault("_egx_table_search_keys_seen", {})
    count = int(seen.get(base, 0))
    seen[base] = count + 1
    return f"{base}_{count}"


def reset_table_search_key_counts() -> None:
    st.session_state["_egx_table_search_keys_seen"] = {}


def filter_table_rows(df: pd.DataFrame, query: str) -> pd.DataFrame:
    query = str(query or "").strip()
    if not query or df.empty:
        return df
    terms = [term.lower() for term in query.split() if term.strip()]
    if not terms:
        return df
    text = df.fillna("").astype(str).agg(" ".join, axis=1).str.lower()
    mask = pd.Series(True, index=df.index)
    for term in terms:
        mask &= text.str.contains(re.escape(term), na=False)
    return df.loc[mask]


def _unwrap_dataframe_renderer(func: Any) -> Any:
    seen: set[int] = set()
    current = func
    while getattr(current, "_egx_search_patch", False) and id(current) not in seen:
        seen.add(id(current))
        original = getattr(current, "_egx_original_dataframe", None)
        if original is None:
            break
        current = original
    return current


def _dataframe_renderer():
    return _unwrap_dataframe_renderer(_ORIGINAL_DATAFRAME or st.dataframe)


def searchable_dataframe(
    df: pd.DataFrame,
    *,
    height: int | None = None,
    search_key: str | None = None,
    search_label: str = "Search this table",
    placeholder: str = "Search any column...",
    **dataframe_kwargs: Any,
) -> None:
    if df.empty:
        empty_state("No rows available yet.")
        return
    filtered = df
    if len(df) > 1:
        query = st.text_input(search_label, key=search_key or _table_key(df), placeholder=placeholder)
        filtered = filter_table_rows(df, query)
        if query:
            st.caption(f"Showing {len(filtered):,} of {len(df):,} rows")
    _dataframe_renderer()(filtered, use_container_width=True, hide_index=True, height=height, **dataframe_kwargs)


def professional_table(df: pd.DataFrame, *, height: int | None = None, search_key: str | None = None) -> None:
    searchable_dataframe(df, height=height, search_key=search_key)


def install_dataframe_search_patch() -> None:
    global _ORIGINAL_DATAFRAME, _DATAFRAME_PATCH_INSTALLED
    original = _unwrap_dataframe_renderer(getattr(st, "_egx_original_dataframe", None) or st.dataframe)
    if _DATAFRAME_PATCH_INSTALLED:
        _ORIGINAL_DATAFRAME = original
        return
    _ORIGINAL_DATAFRAME = original

    def _patched_dataframe(data: Any = None, *args: Any, **kwargs: Any):  # noqa: ANN202
        searchable = bool(kwargs.pop("_egx_searchable", True))
        if searchable and isinstance(data, pd.DataFrame) and not data.empty and len(data) > 1:
            filtered = data
            query = st.text_input("Search this table", key=_table_key(data, "egx_auto_table_search"), placeholder="Search any column...")
            filtered = filter_table_rows(data, query)
            if query:
                st.caption(f"Showing {len(filtered):,} of {len(data):,} rows")
            return _dataframe_renderer()(filtered, *args, **kwargs)
        return _dataframe_renderer()(data, *args, **kwargs)

    st.dataframe = _patched_dataframe
    setattr(st, "_egx_original_dataframe", _ORIGINAL_DATAFRAME)
    setattr(st.dataframe, "_egx_search_patch", True)
    setattr(st.dataframe, "_egx_original_dataframe", _ORIGINAL_DATAFRAME)
    _DATAFRAME_PATCH_INSTALLED = True


def key_value_table(data: dict[str, Any], *, key_label: str = "Metric", value_label: str = "Value") -> None:
    rows = []
    for key, value in data.items():
        if isinstance(value, (dict, list, tuple, set)):
            value = ", ".join(str(item) for item in value) if not isinstance(value, dict) else "; ".join(f"{k}: {v}" for k, v in value.items())
        rows.append({key_label: str(key).replace("_", " ").title(), value_label: "" if value is None else str(value)})
    professional_table(pd.DataFrame(rows))


def data_gap_box(title: str, text: str, action: str | None = None) -> None:
    action_html = f"<br><b>Next action:</b> {action}" if action else ""
    st.markdown(
        f'<div class="egx-box egx-warning"><b>{title}</b><br>{text}{action_html}</div>',
        unsafe_allow_html=True,
    )
