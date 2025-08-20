import os
import json
import uuid
import requests
import streamlit as st

# Optional drag & drop helper (graceful fallback if unavailable)
try:
    from streamlit_sortables import sort_items  # pip install streamlit-sortables
    HAS_SORT = True
except Exception:
    HAS_SORT = False

st.set_page_config(page_title="Content Brief Generator", layout="wide")

# ---- Load external styles ----
def load_css(path: str = "styles.css"):
    try:
        with open(path, "r", encoding="utf-8") as f:
            st.markdown(f"<style>{f.read()}</style>", unsafe_allow_html=True)
    except FileNotFoundError:
        pass

load_css()

# --- Config ---
WEBHOOK_URL = st.secrets.get("N8N_WEBHOOK_URL") or os.getenv("N8N_WEBHOOK_URL")
AUTH_HEADER = st.secrets.get("N8N_AUTH_HEADER") or os.getenv("N8N_AUTH_HEADER")

# --- Constants ---
GROUPS = ["MainContent", "ContextualBorder", "SupplementaryContent"]
ANSWER_TYPES = ["Auto", "EDA", "DDA", "L+LD", "S L+LD", "EOE"]
HEADING_LEVELS = ["H2", "H3", "H4", "H5", "H6"]

# --- Session bootstrap ---
if "session_id" not in st.session_state:
    st.session_state["session_id"] = str(uuid.uuid4())

# Each group keeps a list of section dicts:
# {id, heading, level(H2..H6), content, answer_type, lock, gen_preceding}
if "sections" not in st.session_state:
    st.session_state["sections"] = {g: [] for g in GROUPS}

# H1 state + flags
st.session_state.setdefault("H1_text", "")
st.session_state.setdefault("H1_lock", False)
st.session_state.setdefault("feedback", "")
st.session_state.setdefault("hydrated_once", False)           # only hydrate once
st.session_state.setdefault("_h1_widget_rendered", False)     # avoid late writes
st.session_state.setdefault("_h1_user_initialized", False)    # user typed or prefilled

def _new_section(heading="", level="H2", content="", answer_type="Auto", lock=False, gen_preceding=False):
    return {
        "id": str(uuid.uuid4()),
        "heading": heading or "",
        "level": level if level in HEADING_LEVELS else "H2",
        "content": content or "",
        "answer_type": answer_type if answer_type in ANSWER_TYPES else "Auto",
        "lock": bool(lock),
        "gen_preceding": bool(gen_preceding),
    }

def _hydrate_from_pending():
    """Apply incoming values BEFORE widgets are created (one-time on load)."""
    if st.session_state.get("hydrated_once"):
        return
    pending = st.session_state.pop("_pending_hydration", None)
    if not pending:
        return

    # === H1: if an H1 is already specified, ignore webhook H1 (your request) ===
    if not st.session_state.get("H1_text"):
        st.session_state["H1_text"] = pending.get("H1", "") or ""
    # Don't set H1_lock here; user controls it.

    # Groups — create fresh sections from webhook payload
    for group in GROUPS:
        # Keep any pre-created sections (user may add before first webhook)
        existing = st.session_state["sections"][group]
        if existing:
            # If we already have user sections, do not overwrite—append unlocked webhook ones
            for item in pending.get(group, []):
                existing.append(
                    _new_section(
                        heading=item.get("H2", ""),
                        level=item.get("HeadingLevel", "H2"),
                        content=item.get("Methodology", ""),
                        answer_type=item.get("Answer Type", "Auto"),
                    )
                )
        else:
            st.session_state["sections"][group] = [
                _new_section(
                    heading=item.get("H2", ""),
                    level=item.get("HeadingLevel", "H2"),
                    content=item.get("Methodology", ""),
                    answer_type=item.get("Answer Type", "Auto"),
                )
                for item in pending.get(group, [])
            ]

    # Optional feedback
    if "feedback" in pending and not st.session_state.get("feedback"):
        st.session_state["feedback"] = pending["feedback"]
    st.session_state["hydrated_once"] = True

_hydrate_from_pending()

def _build_headers():
    headers = {"Content-Type": "application/json"}
    if AUTH_HEADER:
        try:
            name, value = AUTH_HEADER.split(":", 1)
            headers[name.strip()] = value.strip()
        except ValueError:
            st.warning("N8N_AUTH_HEADER must be 'Header-Name: value'; ignoring.")
    return headers

def _normalize_n8n_response(resp):
    """
    Accepts list, {"output": {...}}, dict, or raw string.
    Returns dict with H1/MainContent/ContextualBorder/SupplementaryContent.
    Item keys may include: H2, Methodology, HeadingLevel, Answer Type
    """
    if resp is None:
        return {}
    if isinstance(resp, str):
        return {"raw": resp}
    data = resp
    if isinstance(data, list):
        data = data[0] if data else {}
    if isinstance(data, dict) and "output" in data and isinstance(data["output"], dict):
        data = data["output"]
    return {
        "H1": data.get("H1", ""),
        "MainContent": data.get("MainContent", []),
        "ContextualBorder": data.get("ContextualBorder", []),
        "SupplementaryContent": data.get("SupplementaryContent", []),
    }

def call_n8n(payload: dict) -> dict:
    """Send payload to n8n and return normalized dict."""
    if not WEBHOOK_URL:
        st.error("N8N webhook URL is not configured. Set N8N_WEBHOOK_URL.")
        return {}

    use_status = hasattr(st, "status")
    if use_status:
        with st.status("Generating Outline…", expanded=False) as status:
            status.update(label="Generating Outline…", state="running")
            r = requests.post(WEBHOOK_URL, json=payload, timeout=90, headers=_build_headers())
            status.update(label=f"Received HTTP {r.status_code}", state="running")
            r.raise_for_status()
            try:
                raw = r.json()
            except json.JSONDecodeError:
                raw = r.text.strip()
            data = _normalize_n8n_response(raw)
            status.update(label="Parsed response", state="complete")
            return data
    else:
        with st.spinner("Generating Outline…"):
            r = requests.post(WEBHOOK_URL, json=payload, timeout=90, headers=_build_headers())
            r.raise_for_status()
            try:
                raw = r.json()
            except json.JSONDecodeError:
                raw = r.text.strip()
            return _normalize_n8n_response(raw)

def _safe_rerun():
    try:
        st.rerun()
    except Exception:
        try:
            st.experimental_rerun()
        except Exception:
            pass

# ---- Helpers: reorder & CRUD ----
def _reorder_by_ids(group, new_id_order):
    id_to_item = {s["id"]: s for s in st.session_state["sections"][group]}
    st.session_state["sections"][group] = [id_to_item[i] for i in new_id_order if i in id_to_item]

def _move(group, idx, delta):
    lst = st.session_state["sections"][group]
    new_idx = max(0, min(len(lst) - 1, idx + delta))
    if new_idx != idx:
        lst[idx], lst[new_idx] = lst[new_idx], lst[idx]

def _insert(group, idx):
    lst = st.session_state["sections"][group]
    lst.insert(idx, _new_section())

def _append(group):
    st.session_state["sections"][group].append(_new_section())

def _remove(group, idx):
    lst = st.session_state["sections"][group]
    if 0 <= idx < len(lst):
        lst.pop(idx)

def _indent_str(level):
    # convert H2..H6 -> 0..4 indents
    n = max(0, HEADING_LEVELS.index(level))
    return " " * n  # em spaces

def _merge_response_into_state(resp):
    """
    Merge webhook response into current UI state:
    - H1: IGNORE if user has already specified any H1 (your request).
    - Sections: index-based replace ONLY for UNLOCKED sections.
    """
    if not resp:
        return

    # H1: ignore if user has initialized H1, otherwise set only if we can safely write pre-widget
    if not st.session_state.get("_h1_user_initialized") and not st.session_state.get("_h1_widget_rendered"):
        st.session_state["H1_text"] = resp.get("H1", st.session_state.get("H1_text", "")) or st.session_state.get("H1_text", "")

    for group in GROUPS:
        incoming = resp.get(group, [])
        current = st.session_state["sections"][group]
        max_len = max(len(current), len(incoming))
        new_list = []

        for i in range(max_len):
            if i < len(current) and (i >= len(incoming)):
                new_list.append(current[i])
                continue
            if i >= len(current) and (i < len(incoming)):
                inc = incoming[i]
                new_list.append(
                    _new_section(
                        heading=inc.get("H2", ""),
                        level=inc.get("HeadingLevel", "H2"),
                        content=inc.get("Methodology", ""),
                        answer_type=inc.get("Answer Type", "Auto"),
                    )
                )
                continue

            cur = current[i]
            inc = incoming[i]
            if cur.get("lock"):
                new_list.append(cur)
            else:
                new_list.append(
                    {
                        **cur,  # keep id
                        "heading": inc.get("H2", cur.get("heading", "")),
                        "level": inc.get("HeadingLevel", cur.get("level", "H2")),
                        "content": inc.get("Methodology", cur.get("content", "")),
                        "answer_type": inc.get("Answer Type", cur.get("answer_type", "Auto")),
                        "lock": cur.get("lock", False),
                        "gen_preceding": cur.get("gen_preceding", False),
                    }
                )
        st.session_state["sections"][group] = new_list

# ---- Sidebar: Outline (drag to reorder) ----
with st.sidebar:
    st.markdown("## 🧭 Simple Outline Overview")
    st.caption("Drag to reorder headings. Use < and > to change levels. First item in each group is just a section; H1 is edited in the main panel.")

    if st.button("🆕 New Brief (reset)", use_container_width=True):
        new_id = str(uuid.uuid4())
        st.session_state.clear()
        st.session_state["session_id"] = new_id
        st.session_state["sections"] = {g: [] for g in GROUPS}
        st.session_state["H1_text"] = ""
        st.session_state["H1_lock"] = False
        st.session_state["feedback"] = ""
        _safe_rerun()

    for g in GROUPS:
        st.markdown(f"**{g}**")
        items = st.session_state["sections"][g]
        if not items:
            st.caption("No sections yet.")
            continue

        display_items = [
            f"{_indent_str(s['level'])}{s['level']} • {s['heading'] or '(untitled)'}"
            for s in items
        ]
        ids = [s["id"] for s in items]

        if HAS_SORT:
            _, sorted_ids = sort_items(
                display_items, ids=ids, direction="vertical", key=f"sidebar_sort_{g}"
            )
            if sorted_ids != ids:
                _reorder_by_ids(g, sorted_ids)
                st.experimental_set_query_params(_=str(uuid.uuid4()))
        else:
            st.caption("Install `streamlit-sortables` for drag & drop here.")
        st.markdown("---")

# ---- Page content ----
st.title("Content Brief Generator")

# Mark that the H1 widget is now on the page (prevents later session writes)
def _flag_h1_render():
    st.session_state["_h1_widget_rendered"] = True
    # if user has any text here, mark initialized so webhook H1 is ignored
    if st.session_state.get("H1_text", "").strip():
        st.session_state["_h1_user_initialized"] = True

# === H1 row ===
col_h1, col_lock = st.columns([0.8, 0.2])
with col_h1:
    st.text_input("H1", key="H1_text", placeholder="Primary page title (H1)")
    _flag_h1_render()
with col_lock:
    st.checkbox("Lock H1", key="H1_lock")

# === “before webhook” editing is allowed ===
# Users can add sections at any time, even before first send.

def _level_decrease(level):
    idx = HEADING_LEVELS.index(level)
    return HEADING_LEVELS[min(idx + 1, len(HEADING_LEVELS) - 1)]  # H2 -> H3 -> ... -> H6

def _level_increase(level):
    idx = HEADING_LEVELS.index(level)
    return HEADING_LEVELS[max(idx - 1, 0)]  # H6 -> H5 -> ... -> H2

def _render_answer_type_pills(group, sec_id, current):
    cols = st.columns(len(ANSWER_TYPES))
    out = current
    for i, choice in enumerate(ANSWER_TYPES):
        active = (current == choice)
        label = f"{choice}"
        if cols[i].button(label, key=f"atype_btn_{group}_{sec_id}_{choice}", help={
            "Auto":"Automatic selection",
            "EDA":"Exploratory Data Answer",
            "DDA":"Detailed Direct Answer",
            "L+LD":"List + Light Details",
            "S L+LD":"Summary + L+LD",
            "EOE":"Evidence-Oriented Explanation"
        }.get(choice,"")):
            out = choice
    return out

def render_group(gname: str):
    st.subheader(gname)

    # Top-level add/clear
    add_cols = st.columns([1, 1, 6])
    if add_cols[0].button("➕ Add Section", key=f"add_{gname}"):
        _append(gname); _safe_rerun()
    if add_cols[1].button("🧹 Clear All", key=f"clear_{gname}"):
        st.session_state["sections"][gname] = []; _safe_rerun()
    st.markdown("<div class='divider'></div>", unsafe_allow_html=True)

    items = st.session_state["sections"][gname]

    # Drag-and-drop within group (main body)
    if HAS_SORT and items:
        body_labels = [
            f"{_indent_str(s['level'])}{s['level']} • {s['heading'] or '(untitled)'}"
            for s in items
        ]
        body_ids = [s["id"] for s in items]
        _, sorted_ids = sort_items(
                body_labels, ids=body_ids, direction="vertical", key=f"body_sort_{gname}"
        )
        if sorted_ids != body_ids:
            _reorder_by_ids(gname, sorted_ids)
            st.experimental_set_query_params(_=str(uuid.uuid4()))

    # Sections UI (mock-like)
    for idx, sec in enumerate(items):
        sid = sec["id"]
        st.markdown("<div class='card'>", unsafe_allow_html=True)

        # Row: <  =  >   [ H2 pill ]   [Section title preview]
        r = st.columns([0.15, 0.15, 0.15, 0.5, 0.05])
        if r[0].button("〈", key=f"dec_{gname}_{sid}", help="Indent (increase level number)"):
            sec["level"] = _level_decrease(sec["level"]); _safe_rerun()
        if r[1].button("＝", key=f"eq_{gname}_{sid}", help="Reset to H2"):
            sec["level"] = "H2"; _safe_rerun()
        if r[2].button("〉", key=f"inc_{gname}_{sid}", help="Outdent (decrease level number)"):
            sec["level"] = _level_increase(sec["level"]); _safe_rerun()
        r[3].markdown(f"<div class='level-chip'>{sec['level']}</div>", unsafe_allow_html=True)
        # quick move/delete
        if r[4].button("🗑️", key=f"rm_{gname}_{sid}", help="Remove section"):
            _remove(gname, idx); _safe_rerun()

        st.text_input("Heading", key=f"{gname}_{sid}_heading", value=sec["heading"])
        st.text_area("Description", key=f"{gname}_{sid}_content", value=sec["content"], height=140)

        # Answer Type chips (no Size control)
        st.caption("Answer Type")
        chosen = _render_answer_type_pills(gname, sid, sec["answer_type"])
        sec["answer_type"] = chosen

        # Lock + Generate Preceding + Move controls
        bottom = st.columns([0.9, 1.3, 1, 1])
        with bottom[0]:
            st.checkbox("Lock Section", key=f"{gname}_{sid}_lock", value=sec["lock"])
        with bottom[1]:
            if st.session_state.get(f"{gname}_{sid}_lock", sec["lock"]):
                st.checkbox("Generate Preceding Sections?", key=f"{gname}_{sid}_genprec", value=sec["gen_preceding"])
            else:
                st.session_state[f"{gname}_{sid}_genprec"] = sec["gen_preceding"]
        with bottom[2]:
            if st.button("⬆️ Move Up", key=f"up_{gname}_{sid}"):
                _move(gname, idx, -1); _safe_rerun()
        with bottom[3]:
            if st.button("⬇️ Move Down", key=f"down_{gname}_{sid}"):
                _move(gname, idx, 1); _safe_rerun()

        st.markdown("</div>", unsafe_allow_html=True)
        st.markdown("<div class='divider subtle'></div>", unsafe_allow_html=True)

# Render groups (always available, even pre-webhook)
for g in GROUPS:
    render_group(g)

# Overall feedback
st.text_area("Overall feedback", key="feedback", placeholder="Optional suggestions for the Agent…")

# --- Build & submit payload ---
with st.form("submit_payload"):
    sent = st.form_submit_button("Send / Update")

if sent:
    # Collect the latest widget values back into the model
    for g in GROUPS:
        updated = []
        for sec in st.session_state["sections"][g]:
            sid = sec["id"]
            updated.append({
                **sec,
                "heading": st.session_state.get(f"{g}_{sid}_heading", sec["heading"]),
                "content": st.session_state.get(f"{g}_{sid}_content", sec["content"]),
                "lock": st.session_state.get(f"{g}_{sid}_lock", sec["lock"]),
                "gen_preceding": st.session_state.get(f"{g}_{sid}_genprec", sec["gen_preceding"]),
            })
        st.session_state["sections"][g] = updated

    payload = {
        "session_id": st.session_state["session_id"],
        "H1": {
            "text": st.session_state.get("H1_text", ""),
            "lock": st.session_state.get("H1_lock", False),
            "regenerate": not st.session_state.get("H1_lock", False),
        },
        "feedback": st.session_state.get("feedback", ""),
    }

    # Convert editor state -> webhook arrays, preserving legacy keys
    for g in GROUPS:
        arr = []
        for sec in st.session_state["sections"][g]:
            item = {
                "H2": sec["heading"],                   # legacy field
                "Methodology": sec["content"],
                "HeadingLevel": sec["level"],
                "Answer Type": sec["answer_type"],
                "lock": sec["lock"],
                "regenerate": not sec["lock"],         # backward compat
            }
            if sec["lock"]:
                item["Generate Preceding Sections?"] = "Yes" if sec["gen_preceding"] else "No"
            arr.append(item)
        payload[g] = arr

    resp = call_n8n(payload)
    normalized = resp or {}
    _merge_response_into_state(normalized)
    _safe_rerun()

# Optional: debug
with st.expander("Debug (optional)"):
    st.write("Session ID:", st.session_state["session_id"])
    st.write("Webhook URL set:", bool(WEBHOOK_URL))
    st.json({
        "H1_text": st.session_state.get("H1_text"),
        "H1_lock": st.session_state.get("H1_lock"),
        "sections": st.session_state.get("sections"),
    })
