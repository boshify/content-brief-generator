import os
import json
import requests
import streamlit as st

st.set_page_config(page_title="Content Brief Generator")

# --- Config: use a KEY name for secrets/env ---
# Put the URL in .streamlit/secrets.toml as:
# [default]
# N8N_WEBHOOK_URL = "https://app.aiseoacademy.co/webhook-test/583c883f-c568-46a3-82a5-8102925a61ef"
#
# Or set an environment variable N8N_WEBHOOK_URL with the same value.
WEBHOOK_URL = (
    st.secrets.get("N8N_WEBHOOK_URL")
    or os.getenv("N8N_WEBHOOK_URL")
)

def call_n8n(payload: dict) -> dict:
    """Send a payload to the n8n workflow and return the JSON (or text) response."""
    if not WEBHOOK_URL:
        st.error("N8N webhook URL is not configured. Set N8N_WEBHOOK_URL in secrets or env.")
        return {}

    try:
        response = requests.post(
            WEBHOOK_URL,
            json=payload,
            timeout=90,
            headers={"Content-Type": "application/json"},
        )
        response.raise_for_status()

        # Try JSON first; fall back to text to avoid crashes
        try:
            return response.json()
        except json.JSONDecodeError:
            text = response.text.strip()
            return {"raw": text} if text else {}

    except requests.Timeout:
        st.error("Request timed out after 90s. Your n8n workflow may be slow or blocking.")
        return {}
    except requests.RequestException as exc:
        st.error(f"Request failed: {exc}")
        # Optional: show response body if present
        if hasattr(exc, "response") and exc.response is not None:
            body = exc.response.text[:1000]
            st.caption(f"Response body (truncated):\n{body}")
        return {}

def render_group(name: str, items: list) -> None:
    """Render a group of sections with editable fields and regenerate toggles."""
    st.subheader(name)
    for idx, item in enumerate(items):
        st.text_input("H2", value=item.get("H2", ""), key=f"{name}_{idx}_H2")
        st.text_area("Content", value=item.get("Methodology", ""), key=f"{name}_{idx}_Methodology")
        st.checkbox("Regenerate?", key=f"{name}_{idx}_regen")
        st.markdown("---")

st.title("Content Brief Generator")

# --- First step: prompt -> call n8n ---
if "data" not in st.session_state:
    with st.form("initial"):
        user_prompt = st.text_area("Describe what you're looking for", key="initial_prompt")
        sent = st.form_submit_button("Send")
    if sent and user_prompt.strip():
        st.session_state["data"] = call_n8n({"prompt": user_prompt.strip()})
        st.rerun()
else:
    data = st.session_state["data"] or {}

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
            "H1": {
                "text": st.session_state.get("H1_text", ""),
                "regenerate": st.session_state.get("H1_regen", False),
            },
            "MainContent": [],
            "ContextualBorder": [],
            "SupplementaryContent": [],
            "feedback": st.session_state.get("feedback", ""),
        }

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

        st.session_state["data"] = call_n8n(payload)
        st.rerun()
