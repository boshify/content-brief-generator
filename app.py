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
ANSWER_TYPES = ["Auto", "EDA", "DDA", "L+LD", "S L+LD", "EOE"]
LEVELS = ["H2", "H3", "H4", "H5", "H6"]

# ---------- Session bootstrap ----------
st.session_state.setdefault("session_id", str(uuid.uuid4()))
st.session_state.setdefault("sections", {g: [] for g in GROUPS})  # per-location lists of dicts
st.session_state.setdefault("H1_text", "")
st.session_state.setdefault("H1_lock", False)
st.session_state.setdefault("feedback", "")
st.session_state.setdefault("hydrated_once", False)
st.session_state.setdefault("_waiting", False)  # overlay while contacting webhook

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

# ---------- Hydration from webhook (one-time pre-widget) ----------
def _hydrate_from_pending():
    if st.session_state.get("hydrated_once"):
        return
    pending = st.session_state.pop("_pending_hydration", None)
    if not pending:
        return

    # Do not overwrite user's H1 if already set
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

# ---------- Sidebar DnD helper ----------
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

# ---------- Build snapshot (SINGLE SOURCE OF TRUTH for sidebar + payload + TSV) ----------
def build_snapshot():
    snap = {
        "session_id": st.session_state["session_id"],
        "H1": {"text": st.session_state.get("H1_text", ""), "lock": st.session_state.get("H1_lock", False)},
        "feedback": st.session_state.get("feedback", ""),
        "MainContent": [],
        "ContextualBorder": [],
        "SupplementaryContent": [],
    }
    for g in GROUPS:
        for sec in st.session_state["sections"][g]:
            # Prefer the current widget value if present; fall back to persisted dict
            hname = st.session_state.get(f"{g}_{sec['id']}_heading_name", sec["heading_name"])
            desc  = st.session_state.get(f"{g}_{sec['id']}_desc", sec["description"])
            atype = st.session_state.get(f"{g}_{sec['id']}_atype", sec["answer_type"])
            lvl   = sec["heading"]
            locked = st.session_state.get(f"{g}_{sec['id']}_lock", sec["lock"])
            subq   = st.session_state.get(f"{g}_{sec['id']}_subseq", sec["subsequent"])

            snap[g].append({
                "H2": hname,
                "Methodology": desc,
                "HeadingLevel": lvl,
                "Answer Type": atype,
                "lock": locked,
                **({"Subsequent Sections?": "Yes" if subq else "No"} if locked else {}),
                "_id": sec["id"],  # id retained only for UI mapping
            })
    return snap

# ---------- UI ----------
st.title("Content Brief Generator")

# H1 row (top)
c1, c2 = st.columns([0.8, 0.2])
with c1:
    st.text_input("H1", key="H1_text", placeholder="Primary page title (H1)")
with c2:
    st.checkbox("Lock H1", key="H1_lock")

# Groups render
def render_group(group):
    st.subheader(GROUP_LABELS[group])
    if st.button("➕ Add Section", key=f"add_{group}"):
        _append_section(group); _safe_rerun()

    items = st.session_state["sections"][group]

    for idx, sec in enumerate(items):
        sid = sec["id"]
        st.markdown("<div class='section-card'>", unsafe_allow_html=True)

        # Top control row (no divider above; CSS handles the card)
        t = st.columns([0.08, 0.08, 0.08, 0.12, 1, 0.30, 0.08], gap="small")
        if t[0].button("〈", key=f"dec_{group}_{sid}", help="Raise level toward H2"):
            sec["heading"] = _level_raise(sec["heading"]); _safe_rerun()
        if t[1].button("＝", key=f"eq_{group}_{sid}", help="Reset to H2"):
            sec["heading"] = "H2"; _safe_rerun()
        if t[2].button("〉", key=f"inc_{group}_{sid}", help="Lower level toward H6"):
            sec["heading"] = _level_lower(sec["heading"]); _safe_rerun()
        t[3].markdown(f"<div class='level-chip'>{sec['heading']}</div>", unsafe_allow_html=True)

        with t[5]:
            loc = st.selectbox(
                "Location",
                GROUPS,
                index=GROUPS.index(group),
                key=f"loc_{group}_{sid}",
                label_visibility="collapsed",
                format_func=lambda v: GROUP_LABELS[v],
            )
            if loc != group:
                moved = st.session_state["sections"][group].pop(idx)
                st.session_state["sections"][loc].append(moved); _safe_rerun()
        with t[6]:
            if st.button("🗑️", key=f"rm_{group}_{sid}", help="Remove section"):
                _remove_item(group, idx); _safe_rerun()

        # Fields — bind to session_state keys so snapshot sees current values
        sec["heading_name"] = st.text_input(
            "Heading Name", key=f"{group}_{sid}_heading_name", value=sec["heading_name"], placeholder="Section title…"
        )
        sec["description"] = st.text_area(
            "Description", key=f"{group}_{sid}_desc", value=sec["description"], height=140
        )
        sec["answer_type"] = st.radio(
            "Answer Type", ANSWER_TYPES, key=f"{group}_{sid}_atype",
            index=ANSWER_TYPES.index(sec["answer_type"]), horizontal=True
        )

        # Bottom controls — tight cluster
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
            if sec["lock"]:
                sec["subsequent"] = st.checkbox("Generate Subsequent Sections?", key=f"{group}_{sid}_subseq", value=sec["subsequent"])
            else:
                # keep key consistent when unlocking
                st.session_state[f"{group}_{sid}_subseq"] = sec["subsequent"]

        st.markdown("</div>", unsafe_allow_html=True)
        # (No divider inside the card; any between-card spacing handled by CSS)

# Render all groups
for g in GROUPS:
    render_group(g)

# Build snapshot AFTER rendering (guaranteed latest widget values)
snapshot = build_snapshot()

# ---------- Sidebar: uses SNAPSHOT for labels + DnD reorder ----------
with st.sidebar:
    st.markdown("## Outline Overview")
    st.caption("Drag to reorder headings. Use 〈 and 〉 to change levels. H1 is edited at the top.")

    for g in GROUPS:
        st.markdown(f"**{GROUP_LABELS[g]}**")
        items = snapshot[g]
        if not items:
            st.caption("No sections yet."); st.markdown("---"); continue

        labels = [f"{_indent(it['HeadingLevel'])}{(it['H2'] or '(untitled)').strip()}" for it in items]
        ids = [it["_id"] for it in items]

        new_order = _dnd(labels, ids, key=f"sidebar_sort_{g}")
        if new_order:
            _reorder_group_by_ids(g, new_order)
            _safe_rerun()
        st.markdown("---")

    st.text_area("Overall feedback", key="feedback", placeholder="Optional suggestions…", height=120)

    with st.form("sidebar_submit"):
        sent = st.form_submit_button("Send / Update", use_container_width=True)

    with st.expander("Debug (optional)"):
        # Show EXACT payload we will send (without _id)
        preview = {
            "session_id": snapshot["session_id"],
            "H1": snapshot["H1"],
            "feedback": snapshot["feedback"],
        }
        for g in GROUPS:
            preview[g] = [{k: v for k, v in d.items() if k != "_id"} for d in snapshot[g]]
        st.json(preview)

# ---------- Send / Update ----------
if sent:
    payload = {
        "session_id": snapshot["session_id"],
        "H1": snapshot["H1"],
        "feedback": snapshot["feedback"],
        "MainContent": [{k: v for k, v in d.items() if k != "_id"} for d in snapshot["MainContent"]],
        "ContextualBorder": [{k: v for k, v in d.items() if k != "_id"} for d in snapshot["ContextualBorder"]],
        "SupplementaryContent": [{k: v for k, v in d.items() if k != "_id"} for d in snapshot["SupplementaryContent"]],
    }
    resp = call_n8n(payload)
    # Merge response respecting locks; never override user's set H1
    if resp:
        if not st.session_state.get("H1_text"):
            st.session_state["H1_text"] = resp.get("H1", st.session_state["H1_text"]) or st.session_state["H1_text"]
        for g in GROUPS:
            inc = resp.get(g, []) or []
            cur = st.session_state["sections"][g]
            max_len = max(len(cur), len(inc))
            merged = []
            for i in range(max_len):
                if i < len(cur) and i >= len(inc):
                    merged.append(cur[i]); continue
                if i >= len(cur) and i < len(inc):
                    item = inc[i]
                    merged.append(_new_section(
                        heading_name=item.get("H2", ""),
                        level=item.get("HeadingLevel", "H2"),
                        description=item.get("Methodology", ""),
                        answer_type=item.get("Answer Type", "Auto"),
                    )); continue
                c = cur[i]; r = inc[i]
                if c.get("lock"):
                    merged.append(c)
                else:
                    merged.append({
                        **c,
                        "heading_name": r.get("H2", c["heading_name"]),
                        "heading": r.get("HeadingLevel", c["heading"]),
                        "description": r.get("Methodology", c["description"]),
                        "answer_type": r.get("Answer Type", c["answer_type"]),
                    })
            st.session_state["sections"][g] = merged
    _safe_rerun()

# ---------- TSV (bottom, includes H1 first row) ----------
rows = []
rows.append(("H1", (st.session_state.get("H1_text") or "").strip(), "", "", "Title"))
for g in GROUPS:
    for sec in st.session_state["sections"][g]:
        location = "Main" if g == "MainContent" else ("Contextual Border" if g == "ContextualBorder" else "Supplementary")
        rows.append((
            sec["heading"],
            (sec["heading_name"] or "").strip(),
            (sec["description"] or "").replace("\t", " ").replace("\r\n", "\\n").replace("\n", "\\n").strip(),
            sec["answer_type"],
            location,
        ))

tsv_lines = ["Heading\tHeading Name\tDescription\tAnswerType\tLocation"]
tsv_lines += ["\t".join(r) for r in rows]
tsv_blob = "\n".join(tsv_lines)

st.markdown("### TSV")
st.code(tsv_blob, language="text")
st.download_button("Download TSV", data=tsv_blob, file_name="content_brief.tsv", mime="text/tab-separated-values")
