import os
import json
import uuid
import requests
import streamlit as st

st.set_page_config(page_title="Content Brief Generator")

# --- Config ---
WEBHOOK_URL = st.secrets.get("N8N_WEBHOOK_URL") or os.getenv("N8N_WEBHOOK_URL")
AUTH_HEADER = st.secrets.get("N8N_AUTH_HEADER") or os.getenv("N8N_AUTH_HEADER")

# --- Session bootstrap ---
if "session_id" not in st.session_state:
    st.session_state["session_id"] = str(uuid.uuid4())
if "data" not in st.session_state:
    st.session_state["data"] = None  # None => pre-response UI

def _hydrate_if_pending():
    """Apply incoming values BEFORE any widgets are created."""
    pending = st.session_state.pop("_pending_hydration", None)
    if not pending:
        return
    # H1
    if "H1" in pending:
        st.session_state["H1_text"] = pending["H1"]
        st.session_state["H1_lock"] = False
    # Groups (dynamic length from webhook)
    for group in ["MainContent", "ContextualBorder", "SupplementaryContent"]:
        for idx, item in enumerate(pending.get(group, [])):
            st.session_state[f"{group}_{idx}_H2"] = item.get("H2", "")
            st.session_state[f"{group}_{idx}_Methodology"] = item.get("Methodology", "")
            st.session_state[f"{group}_{idx}_lock"] = False
    # Optional feedback
    if "feedback" in pending:
        st.session_state["feedback"] = pending["feedback"]

# IMPORTANT: hydrate before any widgets render
_hydrate_if_pending()

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
    """Send payload to n8n and return a normalized dict."""
    if not WEBHOOK_URL:
        st.error("N8N webhook URL is not configured. Set N8N_WEBHOOK_URL.")
        return {}

    use_status = hasattr(st, "status")
    if use_status:
        with st.status("Contacting n8n…", expanded=False) as status:
            status.update(label="Sending request…", state="running")
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
        with st.spinner("Contacting n8n…"):
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

def render_group(name: str, items: list) -> None:
    """
    Render exactly the number of sections received from webhook.
    Widgets are keyed; values come from pre-hydrated session_state.
    """
    if not items:
        return
    st.subheader(name)
    for idx, _ in enumerate(items):
        st.text_input("H2", key=f"{name}_{idx}_H2")
        st.text_area("Content", key=f"{name}_{idx}_Methodology")
        st.checkbox("Lock Section", key=f"{name}_{idx}_lock")
        st.markdown("---")

# ---- Sidebar: New Session button ----
with st.sidebar:
    if st.button("🆕 New Brief (reset)"):
        # Clear everything, create a brand-new session id, and rerun
        new_id = str(uuid.uuid4())
        st.session_state.clear()
        st.session_state["session_id"] = new_id
        st.session_state["data"] = None
        _safe_rerun()

# --- UI ---
st.title("Content Brief Generator")

# Minimal UI before first response: only H1 + Prompt + Send
if st.session_state["data"] is None:
    with st.form("initial"):
        st.text_input("H1", key="H1_text")
        user_prompt = st.text_area("Describe what you're looking for", key="initial_prompt")
        sent = st.form_submit_button("Send")

    if sent and (user_prompt.strip() or st.session_state.get("H1_text", "").strip()):
        payload = {
            "session_id": st.session_state["session_id"],
            "prompt": user_prompt.strip(),
            # Send both for compatibility: lock + regenerate inverse
            "H1": {
                "text": st.session_state.get("H1_text", ""),
                "lock": False,
                "regenerate": True,
            },
        }
        resp = call_n8n(payload)
        normalized = resp or {}

        # Store for rendering AND stage hydration for next run (dynamic counts supported)
        st.session_state["data"] = normalized
        st.session_state["_pending_hydration"] = {
            "H1": normalized.get("H1", ""),
            "MainContent": normalized.get("MainContent", []),
            "ContextualBorder": normalized.get("ContextualBorder", []),
            "SupplementaryContent": normalized.get("SupplementaryContent", []),
        }
        _safe_rerun()

# After first response: full editor UI (dynamic sections)
else:
    data = st.session_state["data"] or {}

    # H1 (value already in session_state from hydration)
    st.text_input("H1", key="H1_text")
    st.checkbox("Lock H1", key="H1_lock")

    render_group("MainContent", data.get("MainContent", []))
    render_group("ContextualBorder", data.get("ContextualBorder", []))
    render_group("SupplementaryContent", data.get("SupplementaryContent", []))

    st.text_area("Overall feedback", key="feedback")

    with st.form("submit_payload"):
        submitted = st.form_submit_button("Submit")

    if submitted:
        # Build payload matching current dynamic counts
        payload = {
            "session_id": st.session_state["session_id"],
            "H1": {
                "text": st.session_state.get("H1_text", ""),
                "lock": st.session_state.get("H1_lock", False),
                # backward compat for existing flows expecting regenerate
                "regenerate": not st.session_state.get("H1_lock", False),
            },
            "MainContent": [],
            "ContextualBorder": [],
            "SupplementaryContent": [],
            "feedback": st.session_state.get("feedback", ""),
        }

        for group in ["MainContent", "ContextualBorder", "SupplementaryContent"]:
            items = data.get(group, [])
            group_items = []
            for idx in range(len(items)):  # dynamic length from webhook
                lock_val = st.session_state.get(f"{group}_{idx}_lock", False)
                group_items.append({
                    "H2": st.session_state.get(f"{group}_{idx}_H2", ""),
                    "Methodology": st.session_state.get(f"{group}_{idx}_Methodology", ""),
                    "lock": lock_val,
                    # backward compat
                    "regenerate": not lock_val,
                })
            payload[group] = group_items

        resp = call_n8n(payload)
        normalized = resp or {}
        st.session_state["data"] = normalized
        st.session_state["_pending_hydration"] = {
            "H1": normalized.get("H1", ""),
            "MainContent": normalized.get("MainContent", []),
            "ContextualBorder": normalized.get("ContextualBorder", []),
            "SupplementaryContent": normalized.get("SupplementaryContent", []),
        }
        _safe_rerun()

# Optional: debug
with st.expander("Debug (optional)"):
    st.write("Session ID:", st.session_state["session_id"])
    st.write("Webhook URL set:", bool(WEBHOOK_URL))
    st.json(st.session_state.get("data") or {})
