import os
import json
import uuid
import requests
import streamlit as st

st.set_page_config(page_title="Content Brief Generator")

# --- Config ---
WEBHOOK_URL = st.secrets.get("N8N_WEBHOOK_URL") or os.getenv("N8N_WEBHOOK_URL")
AUTH_HEADER = st.secrets.get("N8N_AUTH_HEADER") or os.getenv("N8N_AUTH_HEADER")

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
      - raw string (returned as {"raw": str})
    Returns a dict with H1/MainContent/ContextualBorder/SupplementaryContent keys.
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

def _safe_rerun():
    # Works with both newer and older Streamlit versions
    try:
        st.rerun()
    except Exception:
        try:
            st.experimental_rerun()  # fallback for older versions
        except Exception:
            pass  # last resort: continue without rerun

def call_n8n(payload: dict) -> dict:
    """Send payload to n8n and return normalized dict."""
    if not WEBHOOK_URL:
        st.error("N8N webhook URL is not configured. Set N8N_WEBHOOK_URL.")
        return {}

    # Prefer st.status if available; otherwise use spinner
    use_status = hasattr(st, "status")
    ctx = st.status("Contacting n8n…", expanded=False) if use_status else st.spinner("Contacting n8n…")
    if use_status:
        ctx.__enter__().update(label="Sending request…", state="running")
    else:
        ctx.__enter__()

    try:
        r = requests.post(WEBHOOK_URL, json=payload, timeout=90, headers=_build_headers())
        if use_status: st.status("Contacting n8n…").update(label=f"Received HTTP {r.status_code}", state="running")
        r.raise_for_status()
        try:
            raw = r.json()
        except json.JSONDecodeError:
            raw = r.text.strip()
        data = _normalize_n8n_response(raw)
        if use_status: st.status("Contacting n8n…").update(label="Parsed response", state="complete")
        return data
    except requests.Timeout:
        if use_status: st.status("Contacting n8n…").update(label="Timed out", state="error")
        st.error("Request timed out after 90s.")
        return {}
    except requests.RequestException as exc:
        if use_status: st.status("Contacting n8n…").update(label="Request failed", state="error")
        st.error(f"Request failed: {exc}")
        if getattr(exc, "response", None) is not None:
            st.caption(f"Response body (truncated):\n{exc.response.text[:1000]}")
        return {}
    finally:
        ctx.__exit__(None, None, None)

def render_group(name: str, items: list) -> None:
    st.subheader(name)
    for idx, item in enumerate(items):
        st.text_input("H2", value=item.get("H2", ""), key=f"{name}_{idx}_H2")
        st.text_area("Content", value=item.get("Methodology", ""), key=f"{name}_{idx}_Methodology")
        st.checkbox("Regenerate?", key=f"{name}_{idx}_regen")
        st.markdown("---")

# --- Session bootstrapping ---
if "session_id" not in st.session_state:
    st.session_state["session_id"] = str(uuid.uuid4())
if "data" not in st.session_state:
    st.session_state["data"] = None   # None = pre-response UI
if "hydrated" not in st.session_state:
    st.session_state["hydrated"] = False  # ensures we only populate widgets once from response

st.title("Content Brief Generator")

# -------- Minimal UI (before first response): only H1 + Prompt + Send --------
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

        # Store normalized response
        st.session_state["data"] = resp or {}

        # **CRITICAL**: hydrate widget state from response so widgets actually reflect it
        st.session_state["H1_text"] = (resp or {}).get("H1", st.session_state.get("H1_text", ""))
        st.session_state["hydrated"] = False  # we'll hydrate group widgets on next render

        _safe_rerun()

# -------- Editor UI (after first response) --------
else:
    data = st.session_state["data"] or {}

    # One-time hydration for group widgets so their initial values match the response
    if not st.session_state["hydrated"]:
        # H1 already synced above; ensure groups are synced before first render
        for group in ["MainContent", "ContextualBorder", "SupplementaryContent"]:
            for idx, item in enumerate(data.get(group, [])):
                st.session_state[f"{group}_{idx}_H2"] = item.get("H2", "")
                st.session_state[f"{group}_{idx}_Methodology"] = item.get("Methodology", "")
                st.session_state[f"{group}_{idx}_regen"] = False
        st.session_state["feedback"] = st.session_state.get("feedback", "")
        st.session_state["hydrated"] = True

    # H1 (value comes from session_state which we synced from n8n)
    st.text_input("H1", key="H1_text")
    st.checkbox("Regenerate H1", key="H1_regen")

    # Render groups (values come from session_state due to keys set above)
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

        # Update session data & re-hydrate once with the new response
        st.session_state["data"] = resp or {}
        st.session_state["H1_text"] = (resp or {}).get("H1", st.session_state.get("H1_text", ""))
        st.session_state["hydrated"] = False
        _safe_rerun()

# Optional: quick debug
with st.expander("Debug (optional)"):
    st.write("Session ID:", st.session_state["session_id"])
    st.write("Webhook URL set:", bool(WEBHOOK_URL))
    st.json(st.session_state.get("data") or {})
