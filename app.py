import os
import json
import uuid
import requests
import streamlit as st

# Optional drag & drop helper (works with both new/old APIs)
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
GROUP_LABELS = {
    "MainContent": "Main Content",
    "ContextualBorder": "Contextual Border",
    "SupplementaryContent": "Supplementary Content",
}
ANSWER_TYPES = ["Auto", "EDA", "DDA", "L+LD", "S L+LD", "EOE"]
HEADING_LEVELS = ["H2", "H3", "H4", "H5", "H6"]

# --- Session bootstrap ---
st.session_state.setdefault("session_id", str(uuid.uuid4()))
st.session_state.setdefault("sections", {g: [] for g in GROUPS})
st.session_state.setdefault("H1_text", "")
st.session_state.setdefault("H1_lock", False)
st.session_state.setdefault("feedback", "")
st.session_state.setdefault("hydrated_once", False)
st.session_state.setdefault("_h1_widget_rendered", False)
st.session_state.setdefault("_h1_user_initialized", False)
st.session_state.setdefault("_waiting", False)  # overlay blocker

def _new_section(heading="", level="H2", content="", answer_type="Auto", lock=False, gen_sequential=False):
    return {
        "id": str(uuid.uuid4()),
        "heading": heading or "",
        "level": level if level in HEADING_LEVELS else "H2",
        "content": content or "",
        "answer_type": answer_type if answer_type in ANSWER_TYPES else "Auto",
        "lock": bool(lock),
        "gen_sequential": bool(gen_sequential),
    }

def _hydrate_from_pending():
    if st.session_state.get("hydrated_once"):
        return
    pending = st.session_state.pop("_pending_hydration", None)
    if not pending:
        return

    if not st.session_state.get("H1_text"):
        st.session_state["H1_text"] = pending.get("H1", "") or ""

    for group in GROUPS:
        existing = st.session_state["sections"][group]
        incoming = [
            _new_section(
                heading=item.get("H2", ""),
                level=item.get("HeadingLevel", "H2"),
                content=item.get("Methodology", ""),
                answer_type=item.get("Answer Type", "Auto"),
            )
            for item in pending.get(group, [])
        ]
        if existing:
            existing.extend(incoming)
        else:
            st.session_state["sections"][group] = incoming

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

def _overlay_blocker():
    if st.session_state.get("_waiting"):
        st.markdown(
            """
            <div class="overlay">
              <div class="overlay-inner">
                <div class="big-spinner"></div>
                <div class="overlay-text">Generating Outline…</div>
              </div>
            </div>
            """,
            unsafe_allow_html=True,
        )

def call_n8n(payload: dict) -> dict:
    if not WEBHOOK_URL:
        st.error("N8N webhook URL is not configured. Set N8N_WEBHOOK_URL.")
        return {}

    st.session_state["_waiting"] = True
    _overlay_blocker()
    try:
        # ⬆️ timeout to 180s
        r = requests.post(WEBHOOK_URL, json=payload, timeout=180, headers=_build_headers())
        r.raise_for_status()
        try:
            raw = r.json()
        except json.JSONDecodeError:
            raw = r.text.strip()
        data = _normalize_n8n_response(raw)
        return data
    finally:
        st.session_state["_waiting"] = False

def _safe_rerun():
    try:
        st.rerun()
    except Exception:
        try:
            st.experimental_rerun()
        except Exception:
            pass

# ---- DnD helper (supports both sortables APIs) ----
def _dnd_new_order(labels, ids, key):
    if not HAS_SORT or not labels:
        return None
    try:
        new_labels, new_ids = sort_items(labels, ids=ids, direction="vertical", key=key)
        return new_ids if new_ids != ids else None
    except TypeError:
        aug = [f"\u2063{ix}\u2063{labels[ix]}" for ix in range(len(labels))]
        result = sort_items(aug, direction="vertical", key=key)
        new_aug = result[0] if isinstance(result, tuple) else result
        if new_aug == aug:
            return None
        new_indices = []
        for s in new_aug:
            rest = s[1:]
            i_str, _, _ = rest.partition("\u2063")
            new_indices.append(int(i_str) if i_str.isdigit() else 0)
        return [ids[i] for i in new_indices]

# ---- CRUD / reorder ----
def _reorder_by_ids(group, new_id_order):
    id_to_item = {s["id"]: s for s in st.session_state["sections"][group]}
    st.session_state["sections"][group] = [id_to_item[i] for i in new_id_order if i in id_to_item]

def _move(group, idx, delta):
    lst = st.session_state["sections"][group]
    new_idx = max(0, min(len(lst) - 1, idx + delta))
    if new_idx != idx:
        lst[idx], lst[new_idx] = lst[new_idx], lst[idx]

def _insert(group, idx):
    st.session_state["sections"][group].insert(idx + 1, _new_section())

def _append(group):
    st.session_state["sections"][group].append(_new_section())

def _remove(group, idx):
    lst = st.session_state["sections"][group]
    if 0 <= idx < len(lst):
        lst.pop(idx)

def _indent_str(level):
    n = max(0, HEADING_LEVELS.index(level))
    return " " * n  # em spaces

def _merge_response_into_state(resp):
    if not resp:
        return
    if not st.session_state.get("_h1_user_initialized") and not st.session_state.get("_h1_widget_rendered"):
        st.session_state["H1_text"] = resp.get("H1", st.session_state.get("H1_text", "")) or st.session_state.get("H1_text", "")

    for group in GROUPS:
        incoming = resp.get(group, [])
        current = st.session_state["sections"][group]
        max_len = max(len(current), len(incoming))
        new_list = []
        for i in range(max_len):
            if i < len(current) and (i >= len(incoming)):
                new_list.append(current[i]); continue
            if i >= len(current) and (i < len(incoming)):
                inc = incoming[i]
                new_list.append(
                    _new_section(
                        heading=inc.get("H2", ""),
                        level=inc.get("HeadingLevel", "H2"),
                        content=inc.get("Methodology", ""),
                        answer_type=inc.get("Answer Type", "Auto"),
                    )
                ); continue
            cur = current[i]; inc = incoming[i]
            if cur.get("lock"):
                new_list.append(cur)
            else:
                new_list.append({
                    **cur,
                    "heading": inc.get("H2", cur.get("heading", "")),
                    "level": inc.get("HeadingLevel", cur.get("level", "H2")),
                    "content": inc.get("Methodology", cur.get("content", "")),
                    "answer_type": inc.get("Answer Type", cur.get("answer_type", "Auto")),
                    "lock": cur.get("lock", False),
                    "gen_sequential": cur.get("gen_sequential", False),
                })
        st.session_state["sections"][group] = new_list

# ---- Sidebar: Outline + controls (ALWAYS live) ----
with st.sidebar:
    st.markdown("## Outline Overview")
    st.caption("Drag to reorder headings. Use 〈 and 〉 to change levels. H1 is edited in the main panel.")

    # Live outline: reads current widget state (name/level) so it updates instantly
    for g in GROUPS:
        st.markdown(f"**{GROUP_LABELS[g]}**")
        items = st.session_state["sections"][g]
        if not items:
            st.caption("No sections yet."); st.markdown("---"); continue

        def _live_heading(sec):
            key = f"{g}_{sec['id']}_heading"
            return (st.session_state.get(key) or sec["heading"] or "").strip()

        def _live_level(sec):
            key = f"{g}_{sec['id']}_level"  # we don’t store level in a widget key, so fall back
            return st.session_state.get(key, sec["level"])

        display = [
            f"{_indent_str(_live_level(s))}{_live_level(s)} • {_live_heading(s) or '(untitled)'}"
            for s in items
        ]
        ids = [s["id"] for s in items]

        new_order = _dnd_new_order(display, ids, key=f"sidebar_sort_{g}")
        if new_order:
            _reorder_by_ids(g, new_order)
            st.experimental_set_query_params(_=str(uuid.uuid4()))
        st.markdown("---")

    # Footer: H1 + feedback + submit + debug, all in sidebar
    st.text_input("H1", key="H1_text", placeholder="Primary page title (H1)")
    st.checkbox("Lock H1", key="H1_lock")
    st.text_area("Overall feedback", key="feedback", placeholder="Optional suggestions…", height=120)

    with st.form("sidebar_submit"):
        sent = st.form_submit_button("Send / Update", use_container_width=True)

    with st.expander("Debug (optional)"):
        st.write("Session ID:", st.session_state["session_id"])
        st.write("Webhook URL set:", bool(WEBHOOK_URL))
        st.json({
            "H1_text": st.session_state.get("H1_text"),
            "H1_lock": st.session_state.get("H1_lock"),
            "sections": st.session_state.get("sections"),
        })

# ---- Main content ----
st.title("Content Brief Generator")

def _flag_h1_render():
    st.session_state["_h1_widget_rendered"] = True
    if st.session_state.get("H1_text", "").strip():
        st.session_state["_h1_user_initialized"] = True
_flag_h1_render()

# helpers for level change (LEFT raises to H2; RIGHT lowers to H6)
def _level_raise_toward_h2(level):
    idx = HEADING_LEVELS.index(level)
    return HEADING_LEVELS[max(idx - 1, 0)]

def _level_lower_toward_h6(level):
    idx = HEADING_LEVELS.index(level)
    return HEADING_LEVELS[min(idx + 1, len(HEADING_LEVELS) - 1)]

def render_group(gname: str):
    st.subheader(GROUP_LABELS[gname])

    # Add section
    if st.button("➕ Add Section", key=f"add_{gname}"):
        _append(gname); _safe_rerun()
    st.markdown("<div class='divider'></div>", unsafe_allow_html=True)

    items = st.session_state["sections"][gname]

    # DnD within group on page
    if HAS_SORT and items:
        body_labels = [
            f"{_indent_str(s['level'])}{s['level']} • {(st.session_state.get(f'{gname}_{s['id']}_heading') or s['heading'] or '(untitled)')}"
            for s in items
        ]
        body_ids = [s["id"] for s in items]
        new_order = _dnd_new_order(body_labels, body_ids, key=f"body_sort_{gname}")
        if new_order:
            _reorder_by_ids(gname, new_order)
            st.experimental_set_query_params(_=str(uuid.uuid4()))

    # Cards
    for idx, sec in enumerate(items):
        sid = sec["id"]
        st.markdown("<div class='section-card'>", unsafe_allow_html=True)

        # Tight left-aligned arrow cluster + level pill; Location + Trash top-right
        top = st.columns([0.16, 0.16, 0.16, 0.14, 1, 0.22, 0.08], gap="small")
        if top[0].button("〈", key=f"dec_{gname}_{sid}", help="Raise level toward H2"):
            sec["level"] = _level_raise_toward_h2(sec["level"]); _safe_rerun()
        if top[1].button("＝", key=f"eq_{gname}_{sid}", help="Reset to H2"):
            sec["level"] = "H2"; _safe_rerun()
        if top[2].button("〉", key=f"inc_{gname}_{sid}", help="Lower level toward H6"):
            sec["level"] = _level_lower_toward_h6(sec["level"]); _safe_rerun()
        top[3].markdown(f"<div class='level-chip'>{sec['level']}</div>", unsafe_allow_html=True)

        with top[5]:
            loc = st.selectbox("Location", GROUPS, index=GROUPS.index(gname),
                               key=f"loc_{gname}_{sid}", label_visibility="collapsed")
            if loc != gname:
                moved = st.session_state["sections"][gname].pop(idx)
                st.session_state["sections"][loc].append(moved); _safe_rerun()
        with top[6]:
            if st.button("🗑️", key=f"rm_{gname}_{sid}", help="Remove"):
                _remove(gname, idx); _safe_rerun()

        # Inputs
        st.text_input("Heading Name", key=f"{gname}_{sid}_heading", value=sec["heading"], placeholder="Section title…")
        st.text_area("Description", key=f"{gname}_{sid}_content", value=sec["content"], height=140)

        # Answer Type — compact radio (clear selected state)
        st.radio(
            "Answer Type",
            ANSWER_TYPES,
            key=f"{gname}_{sid}_atype",
            index=ANSWER_TYPES.index(sec["answer_type"]),
            horizontal=True
        )

        # Bottom controls
        bottom = st.columns([0.9, 1.5, 1, 1, 1.2])
        with bottom[0]:
            st.checkbox("Lock Section", key=f"{gname}_{sid}_lock", value=sec["lock"])
        with bottom[1]:
            if st.session_state.get(f"{gname}_{sid}_lock", sec["lock"]):
                st.checkbox("Generate Sequential Sections?", key=f"{gname}_{sid}_genseq", value=sec["gen_sequential"])
            else:
                st.session_state[f"{gname}_{sid}_genseq"] = sec["gen_sequential"]
        with bottom[2]:
            if st.button("⬆️ Up", key=f"up_{gname}_{sid}"):
                _move(gname, idx, -1); _safe_rerun()
        with bottom[3]:
            if st.button("⬇️ Down", key=f"down_{gname}_{sid}"):
                _move(gname, idx, 1); _safe_rerun()
        with bottom[4]:
            if st.button("➕ Insert Below", key=f"ins_{gname}_{sid}"):
                _insert(gname, idx); _safe_rerun()

        st.markdown("</div>", unsafe_allow_html=True)
        st.markdown("<div class='divider subtle section-gap'></div>", unsafe_allow_html=True)

# Render groups
for g in GROUPS:
    render_group(g)

# --- Sidebar "Send/Update" handling ---
if sent:
    # sync back to model
    for g in GROUPS:
        updated = []
        for sec in st.session_state["sections"][g]:
            sid = sec["id"]
            updated.append({
                **sec,
                "heading": st.session_state.get(f"{g}_{sid}_heading", sec["heading"]),
                "content": st.session_state.get(f"{g}_{sid}_content", sec["content"]),
                "answer_type": st.session_state.get(f"{g}_{sid}_atype", sec["answer_type"]),
                "lock": st.session_state.get(f"{g}_{sid}_lock", sec["lock"]),
                "gen_sequential": st.session_state.get(f"{g}_{sid}_genseq", sec["gen_sequential"]),
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

    # Arrays (legacy + new keys)
    for g in GROUPS:
        arr = []
        for sec in st.session_state["sections"][g]:
            item = {
                "H2": sec["heading"],
                "Methodology": sec["content"],
                "HeadingLevel": sec["level"],
                "Answer Type": sec["answer_type"],
                "lock": sec["lock"],
                "regenerate": not sec["lock"],
            }
            if sec["lock"]:
                yn = "Yes" if sec["gen_sequential"] else "No"
                item["Generate Sequential Sections?"] = yn
                item["Generate Preceding Sections?"] = yn
            arr.append(item)
        payload[g] = arr

    resp = call_n8n(payload)
    normalized = resp or {}
    _merge_response_into_state(normalized)
    _safe_rerun()

# ===== TSV EXPORT (bottom of page) =====
# Build a TSV across all groups, preserving on-screen order
rows = []
for g in GROUPS:
    for sec in st.session_state["sections"][g]:
        sid = sec["id"]
        heading_name = st.session_state.get(f"{g}_{sid}_heading", sec["heading"]).strip()
        description = st.session_state.get(f"{g}_{sid}_content", sec["content"]).strip()
        answer_type = st.session_state.get(f"{g}_{sid}_atype", sec["answer_type"])
        rows.append((
            sec["level"],
            heading_name,
            description.replace("\t", " ").replace("\r\n", "\\n").replace("\n", "\\n"),
            answer_type,
            GROUP_LABELS[g].split()[0] if g == "MainContent" else
            ("Contextual Border" if g == "ContextualBorder" else "Supplementary")
        ))

tsv_lines = ["Heading\tHeading Name\tDescription\tAnswerType\tLocation"]
for r in rows:
    tsv_lines.append("\t".join(r))
tsv_blob = "\n".join(tsv_lines)

st.markdown("### Copy TSV")
st.caption("Copy/paste into a spreadsheet. Columns: Heading | Heading Name | Description | AnswerType | Location")
st.code(tsv_blob, language="text")
st.download_button("Download TSV", data=tsv_blob, file_name="content_brief.tsv", mime="text/tab-separated-values")
