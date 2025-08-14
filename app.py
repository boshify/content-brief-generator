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
    st.session_state["data"] = None        # None => pre-response UI
# we'll use this to pass values between reruns BEFORE widgets are created
# so we can hydrate widget state safely
def _hydrate_if_pending():
    pending = st.session_state.pop("_pending_hydration", None)
    if not pending:
        return
    # H1
    if "H1" in pending:
        st.session_state["H1_text"] = pending["H1"]
    # Groups
    for group in ["MainContent", "ContextualBorder", "SupplementaryContent"]:
        for idx, item in enumerate(pending.get(group, [])):
            st.session_state[f"{group}_{idx}_H2"] = item.get("H2", "")
            st.session_state[f"{group}_{idx}_Methodology"] = item.get("Methodology", "")
            st.session_state[f"{group}_{idx}_regen"] = False
    # Feedback (optional)
    if "feedback" in pending:
        st.session_state["feedback"] = pending["feedback"]

# IMPORTANT: hydrate BEFORE any widgets are instantiated
_hydrate_if_pending()


def _build_headers():
    headers = {"Content-Type": "application/json"}
    if AUTH_HEADER:
        try:
            name, value = AUTH_HEADER.split(":", 1)
            headers[name.strip()] = value.strip()
        except ValueError:
            st.warning("N8N_AUTH_HEADER is not in 'Name: value' format; ignoring.")
    return headers


def _normalize_n8n_response(resp):
    """
    Accepts:
      - list -> first item
      - {"output": {...}} -> unwrap
      - dict with final keys
      - raw string -> {"raw": "..."}
    Returns a dict with H1/MainContent/ContextualBorder/SupplementaryContent.
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

    if hasattr(st, "status"):
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
    """Render group sections using already-hydrated session_state values."""
    st.subheader(name)
    for idx, item in enumerate(items):
        st.text_input("H2", key=f"{name}_{idx}_H2")
        st.text_area("Content", key=f"{name}_{idx}_Methodology")
        st.checkbox("Regenerate?", key=f"{name}_{idx}_regen")
        st.markdown("---")


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
            "H1": {"text": st.session_state.get("H1_text", ""), "regenerate": False},
        }
        resp = call_n8n(payload)

        # Store raw data for rendering AND stage hydration for next run
        normalized = resp or {}
        st.session_state["data"] = normalized
        st.session_state["_pending_hydration"] = {
            "H1": normalized.get("H1", ""),
            "MainContent": normalized.get("MainContent", []),
            "ContextualBorder": normalized.get("ContextualBorder", []),
            "SupplementaryContent": normalized.get("SupplementaryContent", []),
            # "feedback": ""  # include if you return it
        }
        _safe_rerun()

else:
    # After first response: full editor UI
    data = st.session_state["data"] or {}

    # H1 (value already in session_state from hydration)
    st.text_input("H1", key="H1_text")
    st.checkbox("Regenerate H1", key="H1_regen")

    render_group("MainContent", data.get("MainContent", []))
    render_group("ContextualBorder", data.get("ContextualBorder", []))
    render_group("SupplementaryContent", data.get("SupplementaryContent", []))

    st.text_area("Overall feedback", key="feedback")

    with st.form("submit_payload"):
        submitted = st.form_submit_button("Submit")

    if submitted:
        payload = {
            "session_id": st.session_state["session_id"],
            "H1": {
                "text": st.session_state.get("H1_text", ""),
                "regenerate": st.session_state.get("H1_regen", False),
            },
            "MainContent": [],
            "ContextualBorder": [],
            "SupplementaryContent": [],
            "feedback": st.session_state.get("feedback", ""),
        }

        # Collect edited items back into payload
        for group in ["MainContent", "ContextualBorder", "SupplementaryContent"]:
            items = data.get(group, [])
            group_items = []
            for idx in range(len(items)):
                group_items.append({
                    "H2": st.session_state.get(f"{group}_{idx}_H2", ""),
                    "Methodology": st.session_state.get(f"{group}_{idx}_Methodology", ""),
                    "regenerate": st.session_state.get(f"{group}_{idx}_regen", False),
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
