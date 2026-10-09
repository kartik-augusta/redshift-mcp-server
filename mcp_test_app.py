"""Local Streamlit client that exercises the MCP browser OAuth flow with Entra ID."""

import base64
import hashlib
import json
import os
import secrets
import time
from pathlib import Path
from urllib.parse import urlencode, urlparse

import httpx
import streamlit as st
from dotenv import load_dotenv


load_dotenv(dotenv_path=Path(__file__).resolve().parent / ".env", override=False)

_issuer = os.getenv("OIDC_ISSUER", "").rstrip("/")
_issuer_path = urlparse(_issuer).path.strip("/").split("/")
_tenant_default = os.getenv("ENTRA_TENANT_ID") or (
    _issuer_path[0] if _issuer_path and _issuer_path[0] != "v2.0" else ""
)
_api_audience = os.getenv("OIDC_AUDIENCE", "")
_api_scope = f"{_api_audience.rstrip('/')}/access_as_user" if _api_audience.startswith("api://") else (
    f"api://{_api_audience}/access_as_user" if _api_audience else ""
)

st.set_page_config(page_title="MCP OAuth test client", page_icon="🧪", layout="wide")
st.title("Redshift MCP desktop OAuth simulator")
st.caption("Simulates the browser sign-in path used by an OAuth-capable AI desktop client.")


@st.cache_resource
def pending_oauth_flows() -> dict:
    """Keep short-lived PKCE state across Streamlit sessions during the browser redirect."""
    return {}


def b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode("ascii").rstrip("=")


def begin_mcp_discovery(mcp_url: str, timeout: int) -> tuple[dict, dict]:
    """Trigger the server's 401 challenge and discover its resource and OIDC metadata."""
    response = httpx.post(
        mcp_url,
        json={"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
            "protocolVersion": "2025-03-26", "capabilities": {},
            "clientInfo": {"name": "streamlit-desktop-oauth-simulator", "version": "1.0.0"},
        }},
        headers={"Content-Type": "application/json", "Accept": "application/json, text/event-stream"},
        timeout=timeout,
    )
    if response.status_code != 401:
        raise RuntimeError(
            f"Expected unauthenticated MCP request to return 401; received HTTP {response.status_code}. "
            "Check that OIDC authentication is enabled on the MCP endpoint."
        )

    challenge = response.headers.get("WWW-Authenticate", "")
    marker = 'resource_metadata="'
    if marker not in challenge:
        raise RuntimeError(f"401 did not advertise resource_metadata. WWW-Authenticate: {challenge or '(missing)'}")
    metadata_url = challenge.split(marker, 1)[1].split('"', 1)[0]
    resource_response = httpx.get(metadata_url, timeout=timeout)
    resource_response.raise_for_status()
    resource_metadata = resource_response.json()
    issuers = resource_metadata.get("authorization_servers") or []
    if not issuers:
        raise RuntimeError("Protected-resource metadata did not list an authorization server.")

    issuer = issuers[0].rstrip("/")
    discovery_url = f"{issuer}/.well-known/openid-configuration"
    oidc_response = httpx.get(discovery_url, timeout=timeout)
    oidc_response.raise_for_status()
    return resource_metadata, oidc_response.json()


def start_authorization(
    mcp_url: str, tenant_id: str, client_id: str, scope: str, redirect_uri: str, timeout: int
) -> str:
    """Discover endpoints, create a PKCE challenge, and return the Entra authorize URL."""
    if not all(value.strip() for value in (tenant_id, client_id, scope, redirect_uri, mcp_url)):
        raise ValueError("MCP URL, tenant ID, client ID, API scope, and redirect URI are required.")

    resource_metadata, oidc = begin_mcp_discovery(mcp_url.strip(), timeout)
    authorization_endpoint = oidc.get("authorization_endpoint")
    token_endpoint = oidc.get("token_endpoint")
    if not authorization_endpoint or not token_endpoint:
        raise RuntimeError("The authorization server discovery document is missing OAuth endpoints.")

    state = secrets.token_urlsafe(32)
    verifier = secrets.token_urlsafe(64)
    challenge = b64url(hashlib.sha256(verifier.encode("ascii")).digest())
    scopes = " ".join(dict.fromkeys(["openid", "profile", *scope.split()]))
    flows = pending_oauth_flows()
    now = time.time()
    for old_state, old_flow in list(flows.items()):
        if old_flow["expires_at"] < now:
            flows.pop(old_state, None)
    flows[state] = {
        "state": state,
        "verifier": verifier,
        "token_endpoint": token_endpoint,
        "redirect_uri": redirect_uri.strip(),
        "scope": scopes,
        "resource": resource_metadata.get("resource", mcp_url),
        "client_id": client_id.strip(),
        "expires_at": now + 600,
    }

    query = urlencode({
        "client_id": client_id.strip(),
        "response_type": "code",
        "redirect_uri": redirect_uri.strip(),
        "response_mode": "query",
        "scope": scopes,
        "state": state,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
    })
    return f"{authorization_endpoint}?{query}"


def finish_authorization(code: str, returned_state: str) -> str:
    """Validate OAuth state and redeem the authorization code with the PKCE verifier."""
    flow = pending_oauth_flows().pop(returned_state, None)
    if not flow:
        raise RuntimeError("No sign-in is pending. Start OAuth again from the app.")
    if flow["expires_at"] < time.time():
        raise RuntimeError("The sign-in attempt expired. Start OAuth again from the app.")

    response = httpx.post(
        flow["token_endpoint"],
        data={
            "grant_type": "authorization_code",
            "client_id": flow["client_id"],
            "code": code,
            "redirect_uri": flow["redirect_uri"],
            "code_verifier": flow["verifier"],
            "scope": flow["scope"],
        },
        timeout=30,
    )
    data = response.json()
    if response.is_error or "access_token" not in data:
        raise RuntimeError(data.get("error_description", response.text[:1000]))
    return data["access_token"]


def handle_oauth_callback() -> None:
    """Handle Entra's redirect back to this local Streamlit app."""
    code = st.query_params.get("code")
    returned_state = st.query_params.get("state")
    error = st.query_params.get("error")
    if not (code or error):
        return
    try:
        if error:
            description = st.query_params.get("error_description", error)
            raise RuntimeError(description)
        if not returned_state:
            raise RuntimeError("The OAuth redirect did not include state.")
        st.session_state.auth_token = finish_authorization(code, returned_state)
        st.session_state.oauth_callback_message = "Entra sign-in succeeded. The access token is ready for MCP requests."
    except Exception as exc:
        st.session_state.oauth_callback_error = str(exc)
    finally:
        st.session_state.authorization_url = None
        st.query_params.clear()
        st.rerun()


def decode_jwt_payload(token: str) -> dict:
    """Decode token claims for display only; the MCP server verifies the signature."""
    parts = token.split(".")
    if len(parts) != 3:
        return {}
    try:
        return json.loads(base64.urlsafe_b64decode(parts[1] + "=" * (-len(parts[1]) % 4)))
    except (ValueError, json.JSONDecodeError):
        return {}


class MCPClient:
    def __init__(self, url: str, headers: dict[str, str], timeout: int):
        self.url = url
        self.headers = headers
        self.http = httpx.Client(timeout=timeout, follow_redirects=False)
        self.session_id = None
        self.protocol_version = None
        self.message_id = 0

    def close(self):
        self.http.close()

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()

    def send(self, method: str, params: dict | None = None, notification=False):
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
            **self.headers,
        }
        if self.session_id:
            headers["Mcp-Session-Id"] = self.session_id
        if self.protocol_version:
            headers["MCP-Protocol-Version"] = self.protocol_version
        body = {"jsonrpc": "2.0", "method": method}
        if params is not None:
            body["params"] = params
        if not notification:
            self.message_id += 1
            body["id"] = self.message_id
        response = self.http.post(self.url, json=body, headers=headers)
        self.session_id = response.headers.get("Mcp-Session-Id", self.session_id)
        if notification:
            if response.status_code not in (200, 202, 204):
                self.raise_for_status(response)
            return None
        if response.status_code != 200:
            self.raise_for_status(response)
        if "text/event-stream" in response.headers.get("content-type", ""):
            for line in response.text.splitlines():
                if line.startswith("data:"):
                    message = json.loads(line[5:].strip())
                    if "error" in message:
                        raise RuntimeError(message["error"].get("message", str(message["error"])))
                    if "result" in message:
                        return message["result"]
            raise RuntimeError("The MCP event stream contained no result.")
        message = response.json()
        if "error" in message:
            raise RuntimeError(message["error"].get("message", str(message["error"])))
        return message.get("result")

    @staticmethod
    def raise_for_status(response):
        detail = response.text[:1000]
        challenge = response.headers.get("WWW-Authenticate")
        if challenge:
            detail += f"\nWWW-Authenticate: {challenge}"
        raise RuntimeError(f"HTTP {response.status_code}: {detail}")

    def initialize(self):
        result = self.send("initialize", {
            "protocolVersion": "2025-03-26", "capabilities": {},
            "clientInfo": {"name": "streamlit-desktop-oauth-simulator", "version": "1.0.0"},
        })
        if not result:
            raise RuntimeError("The MCP server returned an empty initialize result.")
        self.protocol_version = result.get("protocolVersion", "2025-03-26")
        self.send("notifications/initialized", notification=True)
        return result

    def list_tools(self):
        result = self.send("tools/list")
        return result.get("tools", []) if result else []

    def call_tool(self, name: str, arguments: dict):
        return self.send("tools/call", {"name": name, "arguments": arguments})


for key, default in {
    "auth_token": None,
    "authorization_url": None,
    "oauth_callback_message": None,
    "oauth_callback_error": None,
    "tools": [],
    "server_info": None,
}.items():
    if key not in st.session_state:
        st.session_state[key] = default

handle_oauth_callback()

with st.sidebar:
    st.header("Connection")
    url = st.text_input("MCP endpoint", value="http://127.0.0.1:8001/mcp")
    auth_mode = st.selectbox("Authentication", ["Entra OAuth (authorization code + PKCE)", "Bearer token (manual)", "API key", "None"])
    headers = {}
    manual_token = ""
    if auth_mode.startswith("Entra OAuth"):
        with st.expander("One-time test client setup", expanded=False):
            st.write(
                "Register one Entra public client for this local simulator. Add the exact redirect URI below, "
                "enable public client flows, and grant it the MCP API's delegated scope. The simulator does "
                "not use a client secret. Each real desktop vendor may use its own registered client; Entra "
                "does not provide dynamic client registration for arbitrary MCP clients."
            )
        tenant_id = st.text_input("Entra tenant ID", value=_tenant_default)
        client_id = st.text_input(
            "Test client application (client) ID",
            value=os.getenv("MCP_TEST_CLIENT_ID", ""),
            help="This is the local simulator's public client ID. The API app ID belongs in the delegated scope below.",
        )
        redirect_uri = st.text_input("Redirect URI registered on the test client", value=os.getenv("MCP_TEST_REDIRECT_URI", "http://localhost:8000/"))
        scope = st.text_input("MCP API delegated scope", value=os.getenv("MCP_OAUTH_SCOPE", _api_scope), help="Example: api://<API app client ID>/access_as_user")

        if st.button("Start OAuth (401 → Entra sign-in)"):
            try:
                authorization_url = start_authorization(url, tenant_id, client_id, scope, redirect_uri, 30)
                st.session_state.authorization_url = authorization_url
                st.session_state.auth_token = None
                st.session_state.oauth_callback_error = None
                st.session_state.oauth_callback_message = None
            except Exception as exc:
                st.error(str(exc))
        if st.session_state.get("authorization_url"):
            st.markdown(f"[Continue to Microsoft sign-in]({st.session_state.authorization_url})")
            st.caption("After sign-in, Microsoft redirects back here; the app redeems the code with PKCE.")
        if st.session_state.get("oauth_callback_message"):
            st.success(st.session_state.oauth_callback_message)
            st.session_state.oauth_callback_message = None
        if st.session_state.get("oauth_callback_error"):
            st.error(st.session_state.oauth_callback_error)
            st.session_state.oauth_callback_error = None
        if st.session_state.get("auth_token"):
            headers["Authorization"] = f"Bearer {st.session_state.auth_token}"
            st.success("Entra access token ready")
            if st.button("Sign out"):
                st.session_state.auth_token = None
                st.session_state.authorization_url = None
                st.rerun()
    elif auth_mode == "Bearer token (manual)":
        manual_token = st.text_input("Access token", type="password")
        if manual_token:
            headers["Authorization"] = f"Bearer {manual_token.strip()}"
    elif auth_mode == "API key":
        api_key = st.text_input("API key", type="password")
        if api_key:
            headers["x-api-key"] = api_key.strip()
    timeout = int(st.number_input("Request timeout (seconds)", min_value=5, max_value=300, value=60))

    active_token = st.session_state.get("auth_token") if auth_mode.startswith("Entra OAuth") else manual_token
    if active_token:
        claims = decode_jwt_payload(active_token.strip())
        if claims:
            with st.expander("Decoded token claims (display only; server verifies signature)"):
                st.json({key: claims.get(key) for key in ("iss", "aud", "tid", "oid", "sub", "roles", "groups", "scp", "exp") if key in claims})


def connect_and_list_tools():
    if not url.strip():
        raise ValueError("Enter an MCP endpoint URL.")
    with MCPClient(url.strip(), headers, timeout) as client:
        info = client.initialize()
        tools = client.list_tools()
    st.session_state.server_info = info
    st.session_state.tools = tools


if st.button("Connect and load tools", type="primary"):
    try:
        connect_and_list_tools()
        st.success(f"Connected. Server returned {len(st.session_state.tools)} tools.")
    except Exception as exc:
        st.session_state.tools = []
        st.session_state.server_info = None
        st.error(str(exc))

if st.session_state.server_info:
    info = st.session_state.server_info
    st.caption(f"Server: {info.get('serverInfo', {}).get('name', 'unknown')} · Protocol: {info.get('protocolVersion', 'unknown')}")

if st.session_state.tools:
    names = [tool["name"] for tool in st.session_state.tools]
    selected = st.selectbox("Tool", names)
    tool = next(item for item in st.session_state.tools if item["name"] == selected)
    st.write(tool.get("description", ""))
    st.json(tool.get("inputSchema", {}))
    raw_arguments = st.text_area("Tool arguments (JSON)", value="{}", height=180)
    if st.button("Call tool"):
        try:
            arguments = json.loads(raw_arguments or "{}")
            if not isinstance(arguments, dict):
                raise ValueError("Tool arguments must be a JSON object.")
            with MCPClient(url.strip(), headers, timeout) as client:
                client.initialize()
                result = client.call_tool(selected, arguments)
            st.subheader("Result")
            st.json(result)
        except Exception as exc:
            st.error(str(exc))
elif st.session_state.server_info:
    st.info("Connection succeeded, but the server returned no tools.")
