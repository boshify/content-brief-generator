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
        st.warning("⚠️ styles.css not found. Using default Streamlit styles.")

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

if "H1_text" not in st.session_state:
    st.session_state["H1_text"] = ""

if "H1_lock" not in st.session_state:
    st.session_state["H1_lock"] = False

if "feedback" not in st.session_state:
    st.session_state["feedback"] = ""

if "hydrated_once" not in st.session_state:
    st.session_state["hydrated_once"] = False  # prevent double-hydration

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
    # H1
    st.session_state["H1_text"] = pending.get("H1", "") or ""
    st.session_state["H1_lock"] = False

    # Groups — create fresh sections from webhook payload
    for group in GROUPS:
        st.session_state["sections"][group] = []
        for item in pending.get(group, []):
            st.session_state["sections"][group].append(
                _new_section(
                    heading=item.get("H2", ""),                     # backward compat
                    level=item.get("HeadingLevel", "H2"),
                    content=item.get("Methodology", ""),
                    answer_type=item.get("Answer Type", "Auto"),
                    lock=False,
                    gen_preceding=False,
                )
            )
    # Optional feedback
    if "feedback" in pending:
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
    Accepts:
      - list -> first item
      - {"output": {...}} -> unwrap
      - dict with final keys
      - raw string -> {"raw": "..."}

    Returns dict with H1/MainContent/ContextualBorder/SupplementaryContent.
    Supports optional keys per item: "HeadingLevel", "Answer Type"
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
    return " " * n  # em spaces for alignment

def _merge_response_into_state(resp):
    """
    Merge webhook response into current UI state:
    - H1: only update if not locked
    - Sections: index-based replace ONLY for unlocked sections (per group)
      Expected response items may include 'H2', 'Methodology', 'HeadingLevel', 'Answer Type'
    """
    if not resp:
        return
    # H1
    if not st.session_state.get("H1_lock", False):
        st.session_state["H1_text"] = resp.get("H1", st.session_state.get("H1_text", "")) or st.session_state.get("H1_text", "")

    for group in GROUPS:
        incoming = resp.get(group, [])
        current = st.session_state["sections"][group]
        max_len = max(len(current), len(incoming))

        new_list = []
        for i in range(max_len):
            if i < len(current) and (i >= len(incoming)):
                # No new data; keep current
                new_list.append(current[i])
                continue
            if i >= len(current) and (i < len(incoming)):
                # New section arriving; accept it
                inc = incoming[i]
                new_list.append(
                    _new_section(
                        heading=inc.get("H2", ""),
                        level=inc.get("HeadingLevel", "H2"),
                        content=inc.get("Methodology", ""),
                        answer_type=inc.get("Answer Type", "Auto"),
                        lock=False,
                        gen_preceding=False,
                    )
                )
                continue

            # Both present
            cur = current[i]
            inc = incoming[i]
            if cur.get("lock"):
                # Keep user edits intact
                new_list.append(cur)
            else:
                # Update from webhook
                new_list.append(
                    {
                        **cur,  # keep id
                        "heading": inc.get("H2", cur.get("heading", "")),
                        "level": inc.get("HeadingLevel", cur.get("level", "H2")),
                        "content": inc.get("Methodology", cur.get("content", "")),
                        "answer_type": inc.get("Answer Type", cur.get("answer_type", "Auto")),
                        # Preserve lock/gen_preceding flags
                        "lock": cur.get("lock", False),
                        "gen_preceding": cur.get("gen_preceding", False),
                    }
                )
        st.session_state["sections"][group] = new_list

# ---- Sidebar: global controls & tree ----
with st.sidebar:
    st.markdown("## 🧭 Outline")

    # Global reset
    if st.button("🆕 New Brief (reset)", use_container_width=True):
        new_id = str(uuid.uuid4())
        st.session_state.clear()
        st.session_state["session_id"] = new_id
        st.session_state["sections"] = {g: [] for g in GROUPS}
        st.session_state["H1_text"] = ""
        st.session_state["H1_lock"] = False
        st.session_state["feedback"] = ""
        _safe_rerun()

    st.caption("Drag to reorder within each group. Indentation reflects heading level.")

    # Sidebar drag & drop list (per-group)
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
            sorted_labels, sorted_ids = sort_items(
                display_items,
                ids=ids,
                direction="vertical",
                key=f"sidebar_sort_{g}",
            )
            # sorted_ids arrives in new order if changed
            if sorted_ids != ids:
                _reorder_by_ids(g, sorted_ids)
                # reflect changes immediately
                st.experimental_set_query_params(_=str(uuid.uuid4()))
        else:
            st.caption("Install `streamlit-sortables` for drag & drop here.")
        st.markdown("---")

# ---- Page content ----
st.title("Content Brief Generator")

# ---- Minimal UI before first response: only H1 + Prompt + Send ----
if not any(st.session_state["sections"][g] for g in GROUPS):
    with st.form("initial"):
        st.text_input("H1", key="H1_text", placeholder="Primary page title (H1)")
        user_prompt = st.text_area("Describe what you're looking for", key="initial_prompt", height=140, placeholder="e.g., Generate a content brief about …")
        sent = st.form_submit_button("Send")

    if sent and (user_prompt.strip() or st.session_state.get("H1_text", "").strip()):
        payload = {
            "session_id": st.session_state["session_id"],
            "prompt": user_prompt.strip(),
            "H1": {
                "text": st.session_state.get("H1_text", ""),
                "lock": False,
                "regenerate": True,  # backward compat
            },
        }
        resp = call_n8n(payload)
        normalized = resp or {}

        # Stage hydration for next run (dynamic counts supported)
        st.session_state["_pending_hydration"] = {
            "H1": normalized.get("H1", ""),
            "MainContent": normalized.get("MainContent", []),
            "ContextualBorder": normalized.get("ContextualBorder", []),
            "SupplementaryContent": normalized.get("SupplementaryContent", []),
        }
        # Apply immediately then rerun
        _hydrate_from_pending()
        _safe_rerun()

# ---- Full editor UI after first response ----
else:
    # H1 row
    col_h1, col_lock = st.columns([0.8, 0.2])
    with col_h1:
        st.text_input("H1", key="H1_text", placeholder="Primary page title (H1)")
    with col_lock:
        st.checkbox("Lock H1", key="H1_lock")

    # CRUD helpers for each group
    def render_group(gname: str):
        st.subheader(gname)

        # Top-level add
        add_cols = st.columns([1, 1, 6])
        if add_cols[0].button("➕ Add Section", key=f"add_{gname}"):
            _append(gname)
            _safe_rerun()
        if add_cols[1].button("🧹 Clear All", key=f"clear_{gname}"):
            st.session_state["sections"][gname] = []
            _safe_rerun()
        st.markdown("<div class='divider'></div>", unsafe_allow_html=True)

        items = st.session_state["sections"][gname]

        # Drag-and-drop within group (main body)
        if HAS_SORT and items:
            body_labels = [
                f"{_indent_str(s['level'])}{s['level']} • {s['heading'] or '(untitled)'}"
                for s in items
            ]
            body_ids = [s["id"] for s in items]
            sorted_labels, sorted_ids = sort_items(
                body_labels, ids=body_ids, direction="vertical", key=f"body_sort_{gname}"
            )
            if sorted_ids != body_ids:
                _reorder_by_ids(gname, sorted_ids)
                st.experimental_set_query_params(_=str(uuid.uuid4()))

        # Sections UI
        for idx, sec in enumerate(items):
            card = st.container()
            with card:
                st.markdown(f"<div class='card'>", unsafe_allow_html=True)
                top = st.columns([5, 1.2, 2, 1.4, 1.4, 1.2])
                with top[0]:
                    st.text_input("Heading", key=f"{gname}_{sec['id']}_heading", value=sec["heading"])
                with top[1]:
                    level_val = st.selectbox(
                        "Level", HEADING_LEVELS, index=HEADING_LEVELS.index(sec["level"]),
                        key=f"{gname}_{sec['id']}_level"
                    )
                with top[2]:
                    ans_val = st.selectbox(
                        "Answer Type", ANSWER_TYPES, index=ANSWER_TYPES.index(sec["answer_type"]),
                        key=f"{gname}_{sec['id']}_atype", help="Sent to JSON like “Answer Type: X”."
                    )
                with top[3]:
                    if st.button("⬆️ Move Up", key=f"up_{gname}_{sec['id']}"):
                        _move(gname, idx, -1)
                        _safe_rerun()
                with top[4]:
                    if st.button("⬇️ Move Down", key=f"down_{gname}_{sec['id']}"):
                        _move(gname, idx, 1)
                        _safe_rerun()
                with top[5]:
                    if st.button("🗑️ Remove", key=f"rm_{gname}_{sec['id']}"):
                        _remove(gname, idx)
                        _safe_rerun()

                # Content
                st.text_area(
                    "Content", key=f"{gname}_{sec['id']}_content",
                    value=sec["content"], height=180, placeholder="Notes, methodology, bullets…"
                )

                # Lock + Generate Preceding
                lock_col, gen_col, insert_col = st.columns([1.2, 2, 2])
                with lock_col:
                    st.checkbox("Lock Section", key=f"{gname}_{sec['id']}_lock", value=sec["lock"])
                with gen_col:
                    if st.session_state.get(f"{gname}_{sec['id']}_lock", sec["lock"]):
                        st.checkbox(
                            "Generate Preceding Sections?",
                            key=f"{gname}_{sec['id']}_genprec",
                            value=sec["gen_preceding"],
                            help="If Yes, tells the Agent to generate subsections before this one."
                        )
                    else:
                        # Ensure hidden state stored
                        st.session_state[f"{gname}_{sec['id']}_genprec"] = sec["gen_preceding"]
                with insert_col:
                    if st.button("➕ Insert Above", key=f"ins_{gname}_{sec['id']}"):
                        _insert(gname, idx)
                        _safe_rerun()

                st.markdown("</div>", unsafe_allow_html=True)  # end card
                st.markdown("<div class='divider subtle'></div>", unsafe_allow_html=True)

    # Render groups
    for g in GROUPS:
        render_group(g)

    # Overall feedback
    st.text_area("Overall feedback", key="feedback", placeholder="Optional suggestions for the Agent…")

    # --- Build & submit payload ---
    with st.form("submit_payload"):
        submitted = st.form_submit_button("Submit")

    if submitted:
        # Collect current state from widgets back into sections model
        for g in GROUPS:
            new_items = []
            for sec in st.session_state["sections"][g]:
                sid = sec["id"]
                new_items.append({
                    "id": sid,
                    "heading": st.session_state.get(f"{g}_{sid}_heading", sec["heading"]),
                    "level": st.session_state.get(f"{g}_{sid}_level", sec["level"]),
                    "content": st.session_state.get(f"{g}_{sid}_content", sec["content"]),
                    "answer_type": st.session_state.get(f"{g}_{sid}_atype", sec["answer_type"]),
                    "lock": st.session_state.get(f"{g}_{sid}_lock", sec["lock"]),
                    "gen_preceding": st.session_state.get(f"{g}_{sid}_genprec", sec["gen_preceding"]),
                })
            st.session_state["sections"][g] = new_items

        # Build payload matching the new schema
        payload = {
            "session_id": st.session_state["session_id"],
            "H1": {
                "text": st.session_state.get("H1_text", ""),
                "lock": st.session_state.get("H1_lock", False),
                "regenerate": not st.session_state.get("H1_lock", False),  # backward compat
            },
            "feedback": st.session_state.get("feedback", ""),
        }

        # Convert editor state -> webhook arrays, preserving legacy keys
        for g in GROUPS:
            arr = []
            for sec in st.session_state["sections"][g]:
                item = {
                    "H2": sec["heading"],                   # legacy name
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

        # Call webhook and merge response (respect locks)
        resp = call_n8n(payload)
        normalized = resp or {}
        _merge_response_into_state(normalized)
        _safe_rerun()

# Optional: debug
with st.expander("Debug (optional)"):
    st.write("Session ID:", st.session_state["session_id"])
    st.write("Webhook URL set:", bool(WEBHOOK_URL))
    # Lightweight JSON for visibility
    debug_payload = {
        "H1_text": st.session_state.get("H1_text"),
        "H1_lock": st.session_state.get("H1_lock"),
        "feedback": st.session_state.get("feedback"),
        "sections": st.session_state.get("sections"),
    }
    st.json(debug_payload)
