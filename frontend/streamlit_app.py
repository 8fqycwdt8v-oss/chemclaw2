"""chemclaw2 Streamlit frontend — Microsoft Entra ID (MSAL) login.

Run locally:
    cp .streamlit/secrets.toml.example .streamlit/secrets.toml   # fill in
    pip install -r requirements.txt
    streamlit run streamlit_app.py

Flow:
  1. Unauthenticated user sees a "Sign in with Microsoft" button.
  2. Entra authenticates them and (because the backend enterprise app has
     "Assignment required") only issues a token if they're in the assigned
     AD group. The redirect lands back here with ?code=…
  3. We exchange the code for a backend-audience access token and forward it
     as a Bearer header on every backend call. The backend re-checks the app
     role and serves the shared knowledge base to any authenticated member.
"""
from __future__ import annotations

import auth
import streamlit as st
from api_client import BackendError, get, health

st.set_page_config(page_title="chemclaw2", page_icon="🧪", layout="wide")


def _handle_redirect() -> None:
    """If Entra redirected back with ?code=…, complete the token exchange."""
    params = dict(st.query_params)
    if "code" in params and auth.get_token() is None:
        result = auth.complete_login(params)
        if result is not None:
            st.query_params.clear()  # drop code/state from the URL
            st.rerun()


def _render_login() -> None:
    st.title("🧪 chemclaw2")
    st.caption("Knowledge-intelligence agent for pharma R&D")
    st.write("Sign in with your organization account to continue.")
    st.link_button("Sign in with Microsoft", auth.login_url(), type="primary")


def _render_app(token: str) -> None:
    user = auth.current_user()
    with st.sidebar:
        st.subheader("Signed in")
        st.write(user.get("name") or user.get("preferred_username") or "(unknown)")
        st.caption(user.get("preferred_username", ""))
        roles = user.get("roles") or []
        if roles:
            st.caption("Roles: " + ", ".join(roles))
        if st.button("Sign out"):
            auth.logout()
            st.rerun()

    st.title("🧪 chemclaw2")

    # Backend connectivity (unauthenticated) — confirms the API is reachable.
    try:
        st.success(f"Backend healthy: {health().get('status', 'ok')}")
    except Exception as exc:  # noqa: BLE001 — surface any connectivity error
        st.error(f"Backend unreachable: {exc}")
        return

    # Authenticated probe — proves the Bearer token + AD-group gate end to end.
    st.subheader("Authenticated backend call")
    path = st.text_input("Endpoint", value="/api/campaigns")
    if st.button("Call backend with my token", type="primary"):
        try:
            data = get(path, token)
            st.success(f"200 OK from {path} — your token passed validation and the group gate.")
            st.json(data)
        except BackendError as e:
            if e.status_code == 401:
                st.warning("401 — token rejected. Sign out and back in.")
            elif e.status_code == 403:
                st.error("403 — you're authenticated but not in the required AD group/role.")
            else:
                st.error(f"{e.status_code}: {e.detail}")


def main() -> None:
    _handle_redirect()
    token = auth.get_token()
    if token is None:
        _render_login()
    else:
        _render_app(token)


if __name__ == "__main__":
    main()
