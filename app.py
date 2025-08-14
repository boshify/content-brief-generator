import os
import requests
import streamlit as st

st.set_page_config(page_title="Content Brief Generator")

WEBHOOK_URL = st.secrets.get("https://app.aiseoacademy.co/webhook-test/583c883f-c568-46a3-82a5-8102925a61ef") or os.getenv("https://app.aiseoacademy.co/webhook-test/583c883f-c568-46a3-82a5-8102925a61ef")


def call_n8n(payload: dict) -> dict:
    """Send a payload to the n8n workflow and return the JSON response."""
    if not WEBHOOK_URL:
        st.error("N8N webhook URL is not configured.")
        return {}
    try:
        response = requests.post(WEBHOOK_URL, json=payload, timeout=90)
        response.raise_for_status()
        return response.json()
    except Exception as exc:  # pragma: no cover - simple error display
        st.error(f"Request failed: {exc}")
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

if "data" not in st.session_state:
    user_prompt = st.text_area("Describe what you're looking for", key="initial_prompt")
    if st.button("Send") and user_prompt.strip():
        st.session_state["data"] = call_n8n({"prompt": user_prompt})
        st.experimental_rerun()
else:
    data = st.session_state["data"]

    st.text_input("H1", value=data.get("H1", ""), key="H1_text")
    st.checkbox("Regenerate H1", key="H1_regen")

    render_group("MainContent", data.get("MainContent", []))
    render_group("ContextualBorder", data.get("ContextualBorder", []))
    render_group("SupplementaryContent", data.get("SupplementaryContent", []))

    st.text_area("Overall feedback", key="feedback")

    if st.button("Submit"):
        payload = {
            "H1": {"text": st.session_state.get("H1_text", ""),
                    "regenerate": st.session_state.get("H1_regen", False)},
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
        st.experimental_rerun()
