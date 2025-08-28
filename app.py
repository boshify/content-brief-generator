import os
import json
import uuid
import requests
import streamlit as st

# Optional: sidebar drag-and-drop (pip install streamlit-sortables)
try:
    from streamlit_sortables import sort_items
    HAS_SORT = True
except Exception:
    HAS_SORT = False

st.set_page_config(page_title="Content Brief Generator", layout="wide")

# ---------- Load styles ----------
def load_css(path: str = "styles.css"):
    try:
        with open(path, "r", encoding="utf-8") as f:
            st.markdown(f"<style>{f.read()}</style>", unsafe_allow_html=True)
    except FileNotFoundError:
        pass

load_css()

# ---------- Config ----------
WEBHOOK_URL = st.secrets.get("N8N_WEBHOOK_URL") or os.getenv("N8N_WEBHOOK_URL")
AUTH_HEADER = st.secrets.get("N8N_AUTH_HEADER") or os.getenv("N8N_AUTH_HEADER")

GROUPS = ["MainContent", "ContextualBorder", "SupplementaryContent"]
GROUP_LABELS = {
    "MainContent": "Main Content",
    "ContextualBorder": "Contextual Border",
    "SupplementaryContent": "Supplementary Content",
}
ANSWER_TYPES = ["Auto", "EDA", "DDA", "L+LD", "S+L+LD", "EOE"]
LEVELS = ["H2", "H3", "H4", "H5", "H6"]

# ---------- Session bootstrap ----------
st.session_state.setdefault("session_id", str(uuid.uuid4()))
st.session_state.setdefault("sections", {g: [] for g in GROUPS})
st.session_state.setdefault("H1_text", "")
st.session_state.setdefault("H1_lock", False)
st.session_state.setdefault("feedback", "")
st.session_state.setdefault("hydrated_once", False)
st.session_state.setdefault("_waiting", False)

def _new_section(heading_name="", level="H2", description="", answer_type="Auto", lock=False, gen_subsequent=False):
    return {
        "id": str(uuid.uuid4()),
        "heading": level if level in LEVELS else "H2",
        "heading_name": heading_name or "",
        "description": description or "",
        "answer_type": answer_type if answer_type in ANSWER_TYPES else "Auto",
        "lock": bool(lock),
        "subsequent": bool(gen_subsequent),
    }

# ---------- Normalization helpers ----------
def _normalize_section(item: dict) -> dict:
    return {
        "H2": item.get("H2", ""),
        "Methodology": item.get("Methodology", ""),
        "HeadingLevel": item.get("HeadingLevel", "H2"),
        "Answer Type": item.get("Answer Type", "Auto"),
        "lock": bool(item.get("lock", False)),
        "Subsequent Sections?": item.get("Subsequent Sections?", "No"),
    }

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

    h1_val = data.get("H1", "")
    h1_text = str(h1_val) if not isinstance(h1_val, dict) else h1_val.get("text", "")

    return {
        "H1": h1_text,
        "MainContent": [_normalize_section(it) for it in data.get("MainContent", [])],
        "ContextualBorder": [_normalize_section(it) for it in data.get("ContextualBorder", [])],
        "SupplementaryContent": [_normalize_section(it) for it in data.get("SupplementaryContent", [])],
        "feedback": data.get("feedback", ""),
    }

# ---------- Hydration ----------
def _hydrate_from_pending():
    if st.session_state.get("hydrated_once"):
        pass
    pending = st.session_state.pop("_pending_hydration", None)
    if not pending:
        return

    if not st.session_state.get("H1_text"):
        st.session_state["H1_text"] = pending.get("H1", "") or ""

    for group in GROUPS:
        incoming = pending.get(group, []) or []
        for item in incoming:
            st.session_state["sections"][group].append(
                _new_section(
                    heading_name=item.get("H2", ""),
                    level=item.get("HeadingLevel", "H2"),
                    description=item.get("Methodology", ""),
                    answer_type=item.get("Answer Type", "Auto"),
                    lock=item.get("lock", False),
                    gen_subsequent=(item.get("Subsequent Sections?", "No") == "Yes"),
                )
            )
    if "feedback" in pending and not st.session_state.get("feedback"):
        st.session_state["feedback"] = pending["feedback"]

    st.session_state["hydrated_once"] = True

_hydrate_from_pending()

# ---------- HTTP ----------
def _build_headers():
    headers = {"Content-Type": "application/json"}
    if AUTH_HEADER:
        try:
            name, value = AUTH_HEADER.split(":", 1)
            headers[name.strip()] = value.strip()
        except ValueError:
            st.warning("N8N_AUTH_HEADER must be 'Header-Name: value'; ignoring.")
    return headers

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
        r = requests.post(WEBHOOK_URL, json=payload, timeout=180, headers=_build_headers())
        r.raise_for_status()
        try:
            raw = r.json()
        except json.JSONDecodeError:
            raw = r.text.strip()
        return _normalize_n8n_response(raw)
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

# ---------- Sidebar DnD ----------
def _dnd(labels, ids, key):
    if not HAS_SORT or not labels:
        return None
    try:
        _labels, new_ids = sort_items(labels, ids=ids, direction="vertical", key=key)
        return new_ids if new_ids != ids else None
    except TypeError:
        result = sort_items(labels, direction="vertical", key=key)
        new_labels = result[0] if isinstance(result, tuple) else result
        if new_labels == labels: return None
        used = [0]*len(labels)
        idxs = []
        for lab in new_labels:
            i = None
            for j, orig in enumerate(labels):
                if lab == orig and not used[j]:
                    i = j; used[j]=1; break
            if i is None:
                i = labels.index(lab)
            idxs.append(i)
        return [ids[i] for i in idxs]

# ---------- Utilities ----------
def _reorder_group_by_ids(group, new_id_order):
    id_to_item = {s["id"]: s for s in st.session_state["sections"][group]}
    st.session_state["sections"][group] = [id_to_item[i] for i in new_id_order if i in id_to_item]

def _move_item(group, idx, delta):
    items = st.session_state["sections"][group]
    new_idx = max(0, min(len(items)-1, idx+delta))
    if new_idx != idx:
        items[idx], items[new_idx] = items[new_idx], items[idx]

def _insert_below(group, idx):
    st.session_state["sections"][group].insert(idx+1, _new_section())

def _append_section(group):
    st.session_state["sections"][group].append(_new_section())

def _remove_item(group, idx):
    items = st.session_state["sections"][group]
    if 0 <= idx < len(items):
        items.pop(idx)

def _level_raise(level):
    i = LEVELS.index(level)
    return LEVELS[max(i-1, 0)]

def _level_lower(level):
    i = LEVELS.index(level)
    return LEVELS[min(i+1, len(LEVELS)-1)]

def _indent(level):
    return " " * LEVELS.index(level)

def _get_widget_value_by_suffix(suffix: str, default):
    for k, v in st.session_state.items():
        if isinstance(k, str) and k.endswith(suffix):
            return v
    return default

# ---------- Snapshot ----------
def build_snapshot():
    snap = {
        "session_id": st.session_state["session_id"],
        "H1": st.session_state.get("H1_text", ""),   # FIX: send H1 as string
        "feedback": st.session_state.get("feedback", ""),
        "MainContent": [],
        "ContextualBorder": [],
        "SupplementaryContent": [],
    }
    for g in GROUPS:
        for sec in st.session_state["sections"][g]:
            sid = sec["id"]
            hname = _get_widget_value_by_suffix(f"_{sid}_heading_name",
                     st.session_state.get(f"{g}_{sid}_heading_name", sec["heading_name"]))
            desc  = _get_widget_value_by_suffix(f"_{sid}_desc",
                     st.session_state.get(f"{g}_{sid}_desc", sec["description"]))
            atype = _get_widget_value_by_suffix(f"_{sid}_atype",
                     st.session_state.get(f"{g}_{sid}_atype", sec["answer_type"]))
            locked = _get_widget_value_by_suffix(f"_{sid}_lock",
                     st.session_state.get(f"{g}_{sid}_lock", sec["lock"]))
            subq   = _get_widget_value_by_suffix(f"_{sid}_subseq",
                     st.session_state.get(f"{g}_{sid}_subseq", sec["subsequent"]))

            snap[g].append({
                "H2": hname,
                "Methodology": desc,
                "HeadingLevel": sec["heading"],
                "Answer Type": atype,
                "lock": bool(locked),
                "Subsequent Sections?": "Yes" if subq else "No",  # FIX: always include
                "_id": sid,
            })
    return snap

# ---------- UI ----------
st.title("Content Brief Generator")

c1, c2 = st.columns([0.8, 0.2])
with c1:
    st.text_input("H1", key="H1_text", placeholder="Primary page title (H1)")
with c2:
    st.checkbox("Lock H1", key="H1_lock")

def render_group(group):
    st.subheader(GROUP_LABELS[group])
    if st.button("➕ Add Section", key=f"add_{group}"):
        _append_section(group); _safe_rerun()

    items = st.session_state["sections"][group]

    for idx, sec in enumerate(items):
        sid = sec["id"]

        st.markdown("<div class='section-card-wrap'></div>", unsafe_allow_html=True)
        st.markdown("<div class='section-card'>", unsafe_allow_html=True)

        t = st.columns([0.08, 0.08, 0.08, 0.12, 1, 0.30, 0.08], gap="small")
        if t[0].button("〈", key=f"dec_{group}_{sid}"):
            sec["heading"] = _level_raise(sec["heading"]); _safe_rerun()
        if t[1].button("＝", key=f"eq_{group}_{sid}"):
            sec["heading"] = "H2"; _safe_rerun()
        if t[2].button("〉", key=f"inc_{group}_{sid}"):
            sec["heading"] = _level_lower(sec["heading"]); _safe_rerun()
        t[3].markdown(f"<div class='level-chip'>{sec['heading']}</div>", unsafe_allow_html=True)

        with t[5]:
            loc = st.selectbox("Location", GROUPS, index=GROUPS.index(group),
                               key=f"loc_{group}_{sid}", label_visibility="collapsed",
                               format_func=lambda v: GROUP_LABELS[v])
            if loc != group:
                moved = st.session_state["sections"][group].pop(idx)
                st.session_state["sections"][loc].append(moved); _safe_rerun()
        with t[6]:
            if st.button("🗑️", key=f"rm_{group}_{sid}"):
                _remove_item(group, idx); _safe_rerun()

        sec["heading_name"] = st.text_input("Heading Name", key=f"{group}_{sid}_heading_name", value=sec["heading_name"])
        sec["description"] = st.text_area("Description", key=f"{group}_{sid}_desc", value=sec["description"], height=140)
        sec["answer_type"] = st.radio("Answer Type", ANSWER_TYPES, key=f"{group}_{sid}_atype",
                                      index=ANSWER_TYPES.index(sec["answer_type"]), horizontal=True)

        b = st.columns([0.14, 0.14, 0.20, 0.22, 0.22], gap="small")
        with b[0]:
            if st.button("⬆️ Up", key=f"up_{group}_{sid}"):
                _move_item(group, idx, -1); _safe_rerun()
        with b[1]:
            if st.button("⬇️ Down", key=f"down_{group}_{sid}"):
                _move_item(group, idx, 1); _safe_rerun()
        with b[2]:
            if st.button("➕ Insert Below", key=f"ins_{group}_{sid}"):
                _insert_below(group, idx); _safe_rerun()
        with b[3]:
            sec["lock"] = st.checkbox("Lock Section", key=f"{group}_{sid}_lock", value=sec["lock"])
        with b[4]:
            sec["subsequent"] = st.checkbox("Generate Subsequent Sections?", key=f"{group}_{sid}_subseq", value=sec["subsequent"])

        st.markdown("</div>", unsafe_allow_html=True)

for g in GROUPS:
    render_group(g)

snapshot = build_snapshot()

# ---------- Sidebar ----------
with st.sidebar:
    st.markdown("## Outline Overview")
    for g in GROUPS:
        st.markdown(f"**{GROUP_LABELS[g]}**")
        items = snapshot[g]
        if not items:
            st.caption("No sections yet."); st.markdown("---"); continue

        labels = [f"{_indent(it['HeadingLevel'])}{(it['H2'] or '(untitled)').strip()}" for it in items]
        ids = [it["_id"] for it in items]
        new_order = _dnd(labels, ids, key=f"sidebar_sort_{g}")
        if new_order:
            _reorder_group_by_ids(g, new_order); _safe_rerun()
        st.markdown("---")

    st.text_area("Overall feedback", key="feedback", placeholder="Optional suggestions…", height=120)
    with st.form("sidebar_submit"):
        sent = st.form_submit_button("Send / Update", use_container_width=True)

    with st.expander("Debug (optional)"):
        preview = {"session_id": snapshot["session_id"], "H1": snapshot["H1"], "feedback": snapshot["feedback"]}
        for g in GROUPS:
            preview[g] = [{k: v for k, v in d.items() if k != "_id"} for d in snapshot[g]]
        st.json(preview)

# ---------- Send ----------
if sent:
    payload = {"session_id": snapshot["session_id"], "H1": snapshot["H1"], "feedback": snapshot["feedback"]}
    for g in GROUPS:
        payload[g] = [{k: v for k, v in d.items() if k != "_id"} for d in snapshot[g]]
    resp = call_n8n(payload)
    if resp:
        staged = {"H1": resp.get("H1", ""), "MainContent": resp.get("MainContent", []),
                  "ContextualBorder": resp.get("ContextualBorder", []),
                  "SupplementaryContent": resp.get("SupplementaryContent", []),
                  "feedback": resp.get("feedback", "")}
        st.session_state["_pending_hydration"] = staged
    _safe_rerun()

# ---------- TSV ----------
rows = []
rows.append(("H1", (st.session_state.get("H1_text") or "").strip(), "", "", "Title"))
for g in GROUPS:
    for sec in st.session_state["sections"][g]:
        location = "Main" if g == "MainContent" else ("Contextual Border" if g == "ContextualBorder" else "Supplementary")
        rows.append((sec["heading"], (sec["heading_name"] or "").strip(),
                     (sec["description"] or "").replace("\t", " ").replace("\r\n", "\\n").replace("\n", "\\n").strip(),
                     sec["answer_type"], location))
tsv_lines = ["Heading\tHeading Name\tDescription\tAnswerType\tLocation"]
tsv_lines += ["\t".join(r) for r in rows]
tsv_blob = "\n".join(tsv_lines)
st.markdown("### TSV")
st.code(tsv_blob, language="text")
st.download_button("Download TSV", data=tsv_blob, file_name="content_brief.tsv", mime="text/tab-separated-values")
