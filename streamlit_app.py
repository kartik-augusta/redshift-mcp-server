"""
Redshift MCP Explorer — AI-powered Streamlit chat UI for Redshift data exploration.

Connects to the Redshift MCP server via Streamable HTTP and provides a
conversational interface powered by Google Gemini.
"""

import streamlit as st
import httpx
import json
import os
import boto3
from dotenv import load_dotenv

load_dotenv()

# ─────────────────────────── Configuration ───────────────────────────────────

MCP_SERVER_URL = f"http://127.0.0.1:{os.environ.get('MCP_SERVER_PORT', '8001')}/mcp"
MCP_API_KEY = os.environ.get("MCP_API_KEY", "")
COGNITO_CLIENT_ID = os.environ.get("COGNITO_CLIENT_ID", "")
COGNITO_REGION = os.environ.get("COGNITO_REGION", "us-east-2")

# AWS Bedrock
BEDROCK_REGION = os.environ.get("BEDROCK_REGION", "us-east-2")
BEDROCK_MODEL_ID = os.environ.get("BEDROCK_MODEL_ID", "us.anthropic.claude-haiku-4-5-20251001-v1:0")
AWS_PROFILE = os.environ.get("AWS_PROFILE", "CapsaAWSDevelopersAI-UserAccess-094092120598")
AWS_ACCESS_KEY_ID = os.environ.get("AWS_ACCESS_KEY_ID", "")
AWS_SECRET_ACCESS_KEY = os.environ.get("AWS_SECRET_ACCESS_KEY", "")
AWS_SESSION_TOKEN = os.environ.get("AWS_SESSION_TOKEN", "")

# ─────────────────────────── Page Config ─────────────────────────────────────

st.set_page_config(
    page_title="Redshift Explorer",
    page_icon="🗄️",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ─────────────────────────── Custom CSS ──────────────────────────────────────

st.markdown("""
<style>
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700&display=swap');

html, body, [class*="css"] {
    font-family: 'Inter', sans-serif;
}

/* ── Header gradient ─────────────────────────────────────────── */
.gradient-header {
    text-align: center;
    padding: 2.5rem 1rem 1.5rem;
}
.gradient-header h1 {
    background: linear-gradient(135deg, #6366f1, #8b5cf6, #06b6d4);
    -webkit-background-clip: text;
    -webkit-text-fill-color: transparent;
    font-size: 2.5rem;
    font-weight: 700;
    margin-bottom: 0.4rem;
    line-height: 1.2;
}
.gradient-header p {
    color: #94a3b8;
    font-size: 1.05rem;
    margin: 0;
}

/* ── Auth card ───────────────────────────────────────────────── */
.auth-card {
    background: linear-gradient(135deg, rgba(30,41,59,.7), rgba(15,23,42,.9));
    border: 1px solid #334155;
    border-radius: 1rem;
    padding: 2rem;
    margin: 1rem 0;
    backdrop-filter: blur(12px);
}

/* ── Auth badge ──────────────────────────────────────────────── */
.auth-badge {
    background: linear-gradient(135deg, #1e293b, #0f172a);
    border: 1px solid #334155;
    border-radius: 0.5rem;
    padding: 0.75rem;
    margin-bottom: 1rem;
}
.auth-badge .status { color: #10b981; font-weight: 600; font-size: 0.85rem; }
.auth-badge .detail { color: #94a3b8; font-size: 0.8rem; margin-top: 0.25rem; }

/* ── Sidebar branding ────────────────────────────────────────── */
.sidebar-brand {
    text-align: center;
    margin-bottom: 1rem;
}
.sidebar-brand h3 {
    background: linear-gradient(135deg, #6366f1, #8b5cf6);
    -webkit-background-clip: text;
    -webkit-text-fill-color: transparent;
    margin: 0;
}

/* ── Tool call badge ─────────────────────────────────────────── */
.tool-badge {
    display: inline-block;
    background: linear-gradient(135deg, #1e1b4b, #312e81);
    border: 1px solid #4338ca;
    border-radius: 0.375rem;
    padding: 0.2rem 0.5rem;
    font-size: 0.75rem;
    color: #a5b4fc;
    font-family: 'Fira Code', monospace;
    margin: 0.15rem;
}

/* ── Quick-prompt buttons ────────────────────────────────────── */
div[data-testid="stVerticalBlock"] button[kind="secondary"] {
    text-align: left !important;
    font-size: 0.82rem !important;
    padding: 0.35rem 0.6rem !important;
    border-color: #334155 !important;
    color: #cbd5e1 !important;
}
div[data-testid="stVerticalBlock"] button[kind="secondary"]:hover {
    border-color: #6366f1 !important;
    color: #e2e8f0 !important;
    background: rgba(99,102,241,.08) !important;
}

/* ── Hide Streamlit branding ─────────────────────────────────── */
#MainMenu { visibility: hidden; }
footer { visibility: hidden; }
header { visibility: hidden; }
</style>
""", unsafe_allow_html=True)


# ─────────────────────────── Session State Init ──────────────────────────────

_DEFAULTS = {
    "authenticated": False,
    "auth_method": None,
    "user_identity": None,
    "messages": [],
    "chat_history": [],
    "mcp_tools": None,
}
for key, val in _DEFAULTS.items():
    if key not in st.session_state:
        st.session_state[key] = val


# ─────────────────────── MCP Streamable HTTP Client ──────────────────────────


class MCPClient:
    """Synchronous HTTP client for the MCP Streamable HTTP protocol."""

    def __init__(self, base_url: str, api_key: str):
        self.base_url = base_url
        self.api_key = api_key
        self.session_id: str | None = None
        self._msg_id = 0
        self._http = httpx.Client(timeout=120)

    def close(self):
        self._http.close()

    def _next_id(self) -> int:
        self._msg_id += 1
        return self._msg_id

    def _send(
        self,
        method: str,
        params: dict | None = None,
        is_notification: bool = False,
    ):
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
            "x-api-key": self.api_key,
        }
        if self.session_id:
            headers["Mcp-Session-Id"] = self.session_id

        body: dict = {"jsonrpc": "2.0", "method": method}
        if params is not None:
            body["params"] = params
        if not is_notification:
            body["id"] = self._next_id()

        resp = self._http.post(self.base_url, json=body, headers=headers)

        # Capture session ID from server
        sid = resp.headers.get("mcp-session-id")
        if sid:
            self.session_id = sid

        if is_notification:
            return None

        if resp.status_code != 200:
            raise Exception(f"MCP error {resp.status_code}: {resp.text[:300]}")

        content_type = resp.headers.get("content-type", "")
        if "text/event-stream" in content_type:
            # Parse SSE — look for the JSON-RPC result event
            for line in resp.text.split("\n"):
                if line.startswith("data:"):
                    data = json.loads(line[5:].strip())
                    if "result" in data:
                        return data["result"]
                    if "error" in data:
                        raise Exception(
                            f"MCP error: {data['error'].get('message', data['error'])}"
                        )
            return None

        data = resp.json()
        if "error" in data:
            raise Exception(
                f"MCP error: {data['error'].get('message', data['error'])}"
            )
        return data.get("result")

    # ── High-level helpers ───────────────────────────────────────────────

    def initialize(self):
        result = self._send(
            "initialize",
            {
                "protocolVersion": "2025-03-26",
                "capabilities": {},
                "clientInfo": {"name": "streamlit-ui", "version": "1.0.0"},
            },
        )
        self._send("notifications/initialized", is_notification=True)
        return result

    def list_tools(self) -> list[dict]:
        result = self._send("tools/list")
        return result.get("tools", []) if result else []

    def call_tool(self, name: str, arguments: dict | None = None) -> str:
        result = self._send(
            "tools/call", {"name": name, "arguments": arguments or {}}
        )
        if result and "content" in result:
            texts = [
                c.get("text", "")
                for c in result["content"]
                if c.get("type") == "text"
            ]
            return "\n".join(texts)
        return str(result)


# ────────────────────────── Cognito Auth Helper ──────────────────────────────


def cognito_login(username: str, password: str) -> dict:
    """Authenticate via Cognito USER_PASSWORD_AUTH flow."""
    url = f"https://cognito-idp.{COGNITO_REGION}.amazonaws.com/"
    headers = {
        "Content-Type": "application/x-amz-json-1.1",
        "X-Amz-Target": "AWSCognitoIdentityProviderService.InitiateAuth",
    }
    body = {
        "AuthFlow": "USER_PASSWORD_AUTH",
        "ClientId": COGNITO_CLIENT_ID,
        "AuthParameters": {"USERNAME": username, "PASSWORD": password},
    }

    with httpx.Client(timeout=15) as client:
        resp = client.post(url, json=body, headers=headers)

    if resp.status_code == 200:
        data = resp.json()
        if "ChallengeName" in data:
            raise Exception(
                f"Authentication challenge: {data['ChallengeName']}. "
                "Please complete this in the AWS Console first."
            )
        auth_result = data.get("AuthenticationResult", {})
        return {
            "access_token": auth_result.get("AccessToken"),
            "id_token": auth_result.get("IdToken"),
            "expires_in": auth_result.get("ExpiresIn"),
        }

    error_data = resp.json()
    raise Exception(error_data.get("message", f"Login failed ({resp.status_code})"))


# ────────────────────────── MCP Connection Helpers ───────────────────────────


@st.cache_resource(ttl=300)
def _fetch_mcp_tools() -> list[dict]:
    """Initialize MCP connection and fetch tool list (cached 5 min)."""
    c = MCPClient(MCP_SERVER_URL, MCP_API_KEY)
    try:
        c.initialize()
        return c.list_tools()
    finally:
        c.close()


def _new_mcp_client() -> MCPClient:
    """Create a fresh, initialized MCP client for a chat turn."""
    c = MCPClient(MCP_SERVER_URL, MCP_API_KEY)
    c.initialize()
    return c


# ────────────────────────── Gemini Integration ───────────────────────────────

SYSTEM_PROMPT = (
    "You are a helpful data analyst assistant with access to a Redshift data warehouse. "
    "The warehouse exposes multiple schemas (the exact list is returned by get_allowed_schemas). "
    "Available tools:\n"
    "  • get_allowed_schemas — returns the server's schema allowlist, default schema, and query limits\n"
    "  • list_schemas — returns the schemas that actually exist in the cluster (filtered by allowlist)\n"
    "  • list_tables(schema) — lists tables in a schema\n"
    "  • describe_table(table, schema) — returns columns, types, nullability for a table\n"
    "  • sample_data(table, schema, limit) — returns sample rows for quick exploration\n"
    "  • table_row_count(table, schema) — returns the row count of a table\n"
    "  • search_columns(keyword, schema) — searches for columns matching a keyword\n"
    "  • query_data(sql) — runs a read-only SELECT (max rows capped by server config)\n"
    "  • explain_query(sql) — shows the EXPLAIN plan without executing\n"
    "  • export_to_csv(sql) — exports query results as CSV\n\n"
    "When the user asks a question that needs data, use the tools to explore the schema "
    "first if needed, then construct and run an appropriate SELECT query. "
    "Always qualify table names with the schema (e.g. schema_name.table_name). "
    "Format results clearly with markdown tables when returning rows of data."
)


def _build_bedrock_tools(mcp_tools: list[dict]) -> list[dict]:
    """Convert MCP tool definitions to Bedrock Converse toolConfig format."""
    tools = []
    type_map = {
        "string": "string",
        "number": "number",
        "integer": "integer",
        "boolean": "boolean",
        "array": "array",
        "object": "object",
    }
    for tool in mcp_tools:
        schema = tool.get("inputSchema", {})
        properties = {}
        for name, prop in schema.get("properties", {}).items():
            ptype = prop.get("type", "string").lower()
            properties[name] = {
                "type": type_map.get(ptype, "string"),
                "description": prop.get("description", f"Parameter {name}"),
            }

        input_schema: dict = {"type": "object", "properties": properties}
        required = schema.get("required", [])
        if required:
            input_schema["required"] = required

        tools.append(
            {
                "toolSpec": {
                    "name": tool["name"],
                    "description": tool.get("description", ""),
                    "inputSchema": {"json": input_schema},
                }
            }
        )
    return tools


def _get_bedrock_client(profile_name: str | None = None):
    """Create a Bedrock Runtime client via a boto3 Session.

    First creates a boto3.Session, allowing:
    - Named AWS profile (via profile_name argument or AWS_PROFILE env var)
    - Explicit credentials (AWS_ACCESS_KEY_ID / AWS_SECRET_ACCESS_KEY)
    - Default profile or default AWS credential provider chain (e.g., ~/.aws/credentials, EC2 IAM role)
    Then creates the bedrock-runtime client from that session.
    """
    prof = profile_name or AWS_PROFILE
    session = boto3.Session(profile_name=prof) if prof else boto3.Session()
    return session.client("bedrock-runtime", region_name=BEDROCK_REGION)


def run_chat_turn(
    user_input: str,
    history: list,
    mcp_tools: list[dict],
    status_container,
):
    """
    Send *user_input* to Bedrock (Claude), handle MCP tool calls in a loop,
    and return ``(response_text, updated_history, tool_call_log)``.

    Uses the Bedrock Converse API which natively supports tool_use.
    """
    bedrock = _get_bedrock_client()
    bedrock_tools = _build_bedrock_tools(mcp_tools)

    history = list(history)  # shallow copy
    history.append({"role": "user", "content": [{"text": user_input}]})

    mcp = _new_mcp_client()
    tool_log: list[dict] = []

    try:
        for _ in range(10):  # safety cap on tool-call iterations
            resp = bedrock.converse(
                modelId=BEDROCK_MODEL_ID,
                messages=history,
                system=[{"text": SYSTEM_PROMPT}],
                toolConfig={"tools": bedrock_tools},
            )
            output = resp["output"]["message"]
            history.append(output)

            stop = resp["stopReason"]
            if stop != "tool_use":
                # Model is done — extract text
                texts = [
                    b["text"] for b in output["content"] if "text" in b
                ]
                return "\n".join(texts), history, tool_log

            # Handle tool_use blocks
            tool_results: list[dict] = []
            for block in output["content"]:
                if "toolUse" not in block:
                    continue
                tu = block["toolUse"]
                name = tu["name"]
                tool_use_id = tu["toolUseId"]
                args = tu.get("input", {})

                status_container.write(
                    f"⚙️ Calling **{name}**`({json.dumps(args)})`"
                )

                try:
                    result = mcp.call_tool(name, args)
                    tool_log.append(
                        {"tool": name, "args": args, "result": result[:500], "ok": True}
                    )
                    status_container.write(f"✅ **{name}** succeeded")
                    tool_results.append(
                        {
                            "toolResult": {
                                "toolUseId": tool_use_id,
                                "content": [{"text": result}],
                            }
                        }
                    )
                except Exception as exc:
                    err = str(exc)
                    tool_log.append(
                        {"tool": name, "args": args, "result": err, "ok": False}
                    )
                    status_container.write(f"❌ **{name}** failed: {exc}")
                    tool_results.append(
                        {
                            "toolResult": {
                                "toolUseId": tool_use_id,
                                "content": [{"text": f"Error: {err}"}],
                                "status": "error",
                            }
                        }
                    )

            history.append({"role": "user", "content": tool_results})
    finally:
        mcp.close()

    return (
        "I reached the maximum number of tool calls. Please try a more specific question.",
        history,
        tool_log,
    )


# ─────────────────────────── UI: Login Page ──────────────────────────────────


def render_login_page():
    col1, col2, col3 = st.columns([1, 2, 1])
    with col2:
        st.markdown(
            '<div class="gradient-header">'
            "<h1>🗄️ Redshift Explorer</h1>"
            "<p>AI-powered data exploration for your Redshift warehouse</p>"
            "</div>",
            unsafe_allow_html=True,
        )

        auth_tab = st.radio(
            "auth_method",
            ["🔑 API Key", "👤 Cognito Login", "🎫 JWT Token"],
            horizontal=True,
            label_visibility="collapsed",
        )

        st.markdown('<div class="auth-card">', unsafe_allow_html=True)

        if auth_tab == "🔑 API Key":
            api_key = st.text_input(
                "API Key",
                type="password",
                placeholder="Enter your x-api-key",
            )
            if st.button("Sign In", type="primary", use_container_width=True):
                if api_key and api_key == MCP_API_KEY:
                    st.session_state.authenticated = True
                    st.session_state.auth_method = "api_key"
                    st.session_state.user_identity = "API Key User"
                    st.rerun()
                else:
                    st.error("Invalid API key.")

        elif auth_tab == "👤 Cognito Login":
            if not COGNITO_CLIENT_ID:
                st.warning(
                    "Cognito is not configured. Set COGNITO_CLIENT_ID in `.env`."
                )
            else:
                username = st.text_input("Username", placeholder="Enter your Cognito username")
                password = st.text_input("Password", type="password", placeholder="Enter your password")
                if st.button("Sign In", type="primary", use_container_width=True):
                    if username and password:
                        try:
                            with st.spinner("Authenticating with Cognito…"):
                                tokens = cognito_login(username, password)
                            st.session_state.authenticated = True
                            st.session_state.auth_method = "cognito"
                            st.session_state.user_identity = username
                            st.session_state.cognito_tokens = tokens
                            st.rerun()
                        except Exception as exc:
                            st.error(f"Login failed: {exc}")
                    else:
                        st.warning("Enter both username and password.")

        elif auth_tab == "🎫 JWT Token":
            st.caption("Advanced — paste a pre-obtained Cognito access or ID token.")
            jwt_token = st.text_area(
                "JWT Token", placeholder="eyJhbGciOi…", height=100
            )
            if st.button("Verify & Sign In", type="primary", use_container_width=True):
                token = (jwt_token or "").strip()
                if token and len(token.split(".")) == 3:
                    st.session_state.authenticated = True
                    st.session_state.auth_method = "jwt"
                    st.session_state.user_identity = "JWT User"
                    st.session_state.jwt_token = token
                    st.rerun()
                else:
                    st.error("Invalid JWT format (expected 3 dot-separated parts).")

        st.markdown("</div>", unsafe_allow_html=True)


# ─────────────────────────── UI: Sidebar ─────────────────────────────────────


def render_sidebar():
    with st.sidebar:
        st.markdown(
            '<div class="sidebar-brand"><h3>🗄️ Redshift Explorer</h3></div>',
            unsafe_allow_html=True,
        )

        # Auth badge
        st.markdown(
            f'<div class="auth-badge">'
            f'<div class="status">✅ Authenticated</div>'
            f'<div class="detail">{st.session_state.user_identity} · {st.session_state.auth_method}</div>'
            f"</div>",
            unsafe_allow_html=True,
        )

        st.divider()

        # Quick prompts
        st.markdown("##### 💡 Quick Prompts")
        prompts = [
            "What schemas and tables are available?",
            "Describe the columns of a table",
            "Search for columns containing a keyword",
            "Show sample data from a table",
            "Show row counts for all tables",
        ]
        for p in prompts:
            if st.button(p, use_container_width=True, key=f"qp_{hash(p)}"):
                st.session_state.pending_prompt = p
                st.rerun()


# ─────────────────────────── UI: Chat ────────────────────────────────────────


def render_chat():
    # Load MCP tools once
    if st.session_state.mcp_tools is None:
        with st.spinner("🔌 Connecting to MCP server…"):
            try:
                st.session_state.mcp_tools = _fetch_mcp_tools()
            except Exception as exc:
                st.error(f"Cannot reach MCP server at `{MCP_SERVER_URL}`: {exc}")
                return
        if not st.session_state.mcp_tools:
            st.error("MCP server returned no tools. Is it running?")
            return

    # Top action bar right above the current chat
    top_col1, top_col2, top_col3 = st.columns([5.5, 1.2, 1.2], vertical_alignment="center")
    with top_col1:
        user = st.session_state.get("user_identity", "User")
        st.markdown(
            f"<div style='display:flex; align-items:center; gap:0.6rem;'>"
            f"<span style='font-size:1.15rem; font-weight:700; color:#f8fafc;'>💬 Chat</span>"
            f"<span style='color:#475569;'>•</span>"
            f"<span style='font-size:0.85rem; color:#94a3b8;'>"
            f"👤 {user} · 🗄️ {len(st.session_state.mcp_tools)} tools ready</span>"
            f"</div>",
            unsafe_allow_html=True,
        )
    with top_col2:
        if st.button("🗑️ Clear", use_container_width=True, key="btn_clear_chat", help="Clear conversation history"):
            st.session_state.messages = []
            st.session_state.chat_history = []
            st.rerun()
    with top_col3:
        if st.button("🚪 Logout", use_container_width=True, key="btn_logout", help="Sign out of Redshift Explorer"):
            for k in list(st.session_state.keys()):
                del st.session_state[k]
            st.cache_resource.clear()
            st.rerun()

    st.divider()

    # Welcome message
    if not st.session_state.messages:
        st.session_state.messages.append(
            {
                "role": "assistant",
                "content": (
                    "👋 Welcome to **Redshift Explorer**!\n\n"
                    "I'm your AI data analyst. Ask me anything about your "
                    "Redshift warehouse — I'll explore schemas, run queries, "
                    "and present results right here.\n\n"
                    "**Try asking:**\n"
                    "- _\"What schemas are available?\"_\n"
                    "- _\"Show me the tables in gold_capsaai\"_\n"
                    "- _\"How many rows are in table X?\"_\n"
                    "- _\"Run: SELECT … FROM …\"_"
                ),
            }
        )

    # Render history
    for msg in st.session_state.messages:
        avatar = "🤖" if msg["role"] == "assistant" else "👤"
        with st.chat_message(msg["role"], avatar=avatar):
            st.markdown(msg["content"])
            if msg.get("tool_calls"):
                with st.expander(
                    f"🔧 {len(msg['tool_calls'])} tool call(s)", expanded=False
                ):
                    for tc in msg["tool_calls"]:
                        icon = "✅" if tc["ok"] else "❌"
                        st.markdown(
                            f"**{icon} {tc['tool']}** `{json.dumps(tc['args'])}`"
                        )
                        st.code(tc["result"], language="json")

    # Pending quick prompt
    pending = st.session_state.pop("pending_prompt", None)
    user_input = st.chat_input("Ask about your Redshift data…")
    if pending and not user_input:
        user_input = pending

    if not user_input:
        return


    # Add user message
    st.session_state.messages.append({"role": "user", "content": user_input})
    with st.chat_message("user", avatar="👤"):
        st.markdown(user_input)

    # Generate AI response
    with st.chat_message("assistant", avatar="🤖"):
        status = st.status("Thinking…", expanded=True)
        try:
            text, hist, tc_log = run_chat_turn(
                user_input,
                st.session_state.get("chat_history", []),
                st.session_state.mcp_tools,
                status,
            )
            label = (
                f"Done — {len(tc_log)} tool call{'s' if len(tc_log) != 1 else ''}"
                if tc_log
                else "Done"
            )
            status.update(label=label, state="complete", expanded=False)

            st.session_state.chat_history = hist
            st.markdown(text)

            st.session_state.messages.append(
                {"role": "assistant", "content": text, "tool_calls": tc_log}
            )
        except Exception as exc:
            status.update(label="Error", state="error")
            err = f"❌ Sorry, something went wrong: {exc}"
            st.error(err)
            st.session_state.messages.append({"role": "assistant", "content": err})


# ─────────────────────────── Main ────────────────────────────────────────────


def main():
    if st.session_state.get("authenticated"):
        render_sidebar()
        render_chat()
    else:
        render_login_page()


main()
