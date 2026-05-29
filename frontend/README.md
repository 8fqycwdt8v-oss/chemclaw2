# chemclaw2 frontend (Streamlit + Microsoft Entra ID)

A thin Streamlit UI that authenticates users against **Microsoft Entra ID**
with **MSAL** (authorization-code flow, confidential client) and forwards the
resulting access token to the FastAPI backend as a `Bearer` header.

## Why this shape

Authentication exists only to (a) give each user their own session and
(b) restrict access — front *and* back end — to a single Entra security
group. The generated knowledge base is shared across all authenticated members.

- The frontend acquires an access token **whose audience is the backend API**
  (scope `api://<backend-client-id>/access_as_user`) and forwards it verbatim.
  This is **token pass-through, not On-Behalf-Of (OBO)** — OBO is only needed
  when the backend must call *another* Entra-protected API as the user, which
  chemclaw2 never does (its downstream calls are Anthropic/OpenAI/Postgres/MCP).
- The AD-group gate is enforced primarily by Entra: the backend enterprise app
  has **"Assignment required = Yes"**, and the security group is assigned to an
  **app role**, so Entra won't even mint a token for a non-member. The backend
  re-checks the role claim (`AZURE_REQUIRED_ROLE`) as defence in depth.

## Entra setup (one-time)

You need **two app registrations** in the same tenant.

### 1. Backend API app registration
1. **Expose an API** → set the Application ID URI to `api://<backend-client-id>` →
   **Add a scope** named `access_as_user` (admins + users can consent).
2. **App roles** → create one role, e.g. value `chemclaw.user`, allowed member
   types *Users/Groups*. This value goes in the backend's `AZURE_REQUIRED_ROLE`.
3. **Manifest** → set `accessTokenAcceptedVersion` to `2` so issued tokens carry
   the v2.0 issuer the backend validates.
4. In **Enterprise applications → this app → Properties**, set
   **Assignment required = Yes**; under **Users and groups**, assign your AD
   **security group** to the `chemclaw.user` app role.

Backend env vars (see repo `.env.example`):
`AZURE_TENANT_ID`, `AZURE_BACKEND_CLIENT_ID` (= backend client id),
`AZURE_REQUIRED_ROLE` (= `chemclaw.user`).

### 2. Frontend (Streamlit) app registration
1. **Authentication** → add a **Web** platform with redirect URI
   `http://localhost:8501` (and your deployed URL).
2. **Certificates & secrets** → create a client secret.
3. **API permissions** → add the backend's `access_as_user` delegated
   permission and grant admin consent.

## Run locally
```bash
cd frontend
cp .streamlit/secrets.toml.example .streamlit/secrets.toml   # fill in both app regs
pip install -r requirements.txt
streamlit run streamlit_app.py
```
The backend must be running (default `http://localhost:8080`) — see the repo
root README. Sign in; the app shows your identity and lets you make an
authenticated call to prove the token + group gate work end to end.

## Files
- `streamlit_app.py` — entry point: login button, redirect handling, demo calls.
- `auth.py` — MSAL confidential-client auth-code flow + silent token refresh.
- `api_client.py` — httpx wrapper that attaches the `Bearer` token.
