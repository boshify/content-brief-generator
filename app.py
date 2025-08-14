import os
import json
import uuid
import requests
import streamlit as st

st.set_page_config(page_title="Content Brief Generator")

# --- Config ---
WEBHOOK_URL = st.secrets.get("N8N_WEBHOOK_URL") or os.getenv("N8N_WEBHOOK_URL")
AUTH_HEADER = st.secrets.get("N8N_AUTH_HEADER") or os.getenv("N8N_AUTH_HEADER")

# Create a per-session UUID you can also pass through n8n
if "session_id" not in st.session_state:
    st.session_state["session_id"] = str(uuid.uuid4())


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
    Accepts any of:
      - dict with the final keys
      - dict with {"output": {...}}
      - list with one dict (and possibly {output: {...}})
      - raw text (string) -> return {"raw": "..."}
    and returns a dict like:
      {"H1": "...", "MainContent": [...], "ContextualBorder": [...], "SupplementaryContent": [...]}
    """
    if resp is None:
        return {}

    # If non-JSON text
    if isinstance(resp, str):
        return {"raw": resp}

    data = resp

    # If it's a list, grab the first element
    if isinstance(data, list):
        data = data[0] if data else {}

    # If wrapped in {"output": {...}}, unwrap
    if isinstance(data, dict) and "output" in data and isinstance(data["output"], dict):
        data = data["output"]

    # Make sure we always have the expected keys (no crashes if missing)
    normalized = {
        "H1": data.get("H1", ""),
        "MainContent": data.get("MainContent", []),
        "ContextualBorder": data.get("ContextualBorder", []),
        "SupplementaryContent": data.get("SupplementaryContent", []),
    }
    return normalized


def call_n8n(payload: dict) -> dict:
    """Send a payload to the n8n workflow and return a normalized dict."""
    if not WEBHOOK_URL:
        st.error("N8N webhook URL is not configured. Set N8N_WEBHOOK_URL.")
        return {}

    with st.status("Contacting n8n…", expanded=False) as status:
        status.update(label="Sending request…", state="running")
        try:
            response = requests.post(
                WEBHOOK_URL,
                json=payload,
                timeout=90,
                headers=_build_headers(),
            )
            status.update(label=f"Received HTTP {response.status_code}", state="running")
            response.raise_for_status()

            # Try JSON; fall back to text
            try:
                raw = response.json()
            except json.JSONDecodeError:
                raw = response.text.strip()

            data = _normalize_n8n_response(raw)
            status.update(label="Parsed response", state="complete")
            return data

        except requests.Timeout:
            status.update(label="Timed out", state="error")
            st.error("Request timed out after 90s.")
            return {}
        except requests.RequestException as exc:
            status.update(label="Request failed", state="error")
            st.error(f"Request failed: {exc}")
            if getattr(exc, "response", None) is not None:
                st.caption(f"Response body (truncated):\n{exc.response.text[:1000]}")
            return {}


def render_group(name: str, items: list) -> None:
    """Render a group of sections with editable fields and regenerate toggles."""
    st.subheader(name)
    for idx, item in enumerate(items):
        st.text_input("H2", value=item.get("H2", ""), key=f"{name}_{idx}_H2")
        st.text_area("Content", value=item.get("Methodology", ""), key=f"{name}_{idx}_Methodology")
        st.checkbox("Regenerate?", key=f"{name}_{idx}_regen")
        st.markdown("---")


# --- UI ---
st.title("Content Brief Generator")

# Show only prompt + H1 until we have a normalized response in session_state["data_ready"]
if "data" not in st.session_state:
    st.session_state["data"] = None

# Minimal pre-response UI
if st.session_state["data"] is None:
    with st.form("initial"):
        st.text_input("H1", key="H1_text")
        user_prompt = st.text_area("Describe what you're looking for", key="initial_prompt")
        sent = st.form_submit_button("Send")

    if sent and (user_prompt.strip() or st.session_state.get("H1_text", "").strip()):
        payload = {
            "session_id": st.session_state["session_id"],   # <-- session tracking
            "prompt": user_prompt.strip(),
            "H1": {"text": st.session_state.get("H1_text", ""), "regenerate": False},
        }
        st.session_state["data"] = call_n8n(payload)  # normalized dict
        st.rerun()

else:
    # After first response: show full UI
    data = st.session_state["data"] or {}

    # H1 is editable after response
    st.text_input("H1", value=data.get("H1", ""), key="H1_text")
    st.checkbox("Regenerate H1", key="H1_regen")

    render_group("MainContent", data.get("MainContent", []))
    render_group("ContextualBorder", data.get("ContextualBorder", []))
    render_group("SupplementaryContent", data.get("SupplementaryContent", []))

    st.text_area("Overall feedback", key="feedback")

    with st.form("submit_payload"):
        submitted = st.form_submit_button("Submit")
    if submitted:
        payload = {
            "session_id": st.session_state["session_id"],  # keep passing the same session_id
            "H1": {
                "text": st.session_state.get("H1_text", ""),
                "regenerate": st.session_state.get("H1_regen", False),
            },
            "MainContent": [],
            "ContextualBorder": [],
            "SupplementaryContent": [],
            "feedback": st.session_state.get("feedback", ""),
        }

        # Gather edited items for each group
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

        st.session_state["data"] = call_n8n(payload)  # normalized again
        st.rerun()

# Optional: quick debug toggle
with st.expander("Debug (optional)"):
    st.write("Session ID:", st.session_state["session_id"])
    st.write("Webhook URL set:", bool(WEBHOOK_URL))
    st.json(st.session_state.get("data") or {})
