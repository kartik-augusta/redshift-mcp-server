"""
Redshift MCP Client — Gemini-powered interactive CLI.

Usage:
    python client.py

Env:
    GEMINI_API_KEY  — your Google Gemini API key
    (All Redshift / SSH vars are read by server.py via its own .env)
"""

import asyncio
import json
import os
import re
import sys
from contextlib import AsyncExitStack
from typing import Any

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from mcp.types import Tool

from google import genai
from google.genai import types as genai_types
from dotenv import load_dotenv

load_dotenv()

# ─────────────────────────── ANSI colours ────────────────────────────────────
RESET   = "\033[0m"
BOLD    = "\033[1m"
CYAN    = "\033[96m"
YELLOW  = "\033[93m"
GREEN   = "\033[92m"
RED     = "\033[91m"
MAGENTA = "\033[95m"
DIM     = "\033[2m"
BLUE    = "\033[94m"


def banner() -> None:
    print(f"""
{CYAN}{BOLD}╔══════════════════════════════════════════════════════╗
║   🗄  Redshift MCP Client  ·  Gemini AI               ║
║   Ask questions about your data warehouse in plain    ║
║   English — schemas, tables, descriptions, queries    ║
╚══════════════════════════════════════════════════════╝{RESET}
{DIM}Type  {RESET}{BOLD}exit{RESET}{DIM}  or  {RESET}{BOLD}quit{RESET}{DIM}  to leave.
Type  {RESET}{BOLD}tools{RESET}{DIM}  to list available MCP tools.{RESET}
""")


# ─────────────────────── MCP tool → Gemini function ──────────────────────────

def mcp_tool_to_gemini_function(tool: Tool) -> genai_types.FunctionDeclaration:
    """Convert an MCP Tool definition to a Gemini FunctionDeclaration."""
    schema = tool.inputSchema or {}

    properties: dict[str, Any] = {}
    for name, prop in schema.get("properties", {}).items():
        prop_type = prop.get("type", "string").upper()
        type_map = {
            "STRING":  "STRING",
            "NUMBER":  "NUMBER",
            "INTEGER": "INTEGER",
            "BOOLEAN": "BOOLEAN",
            "ARRAY":   "ARRAY",
            "OBJECT":  "OBJECT",
            "FLOAT":   "NUMBER",
        }
        gemini_type = type_map.get(prop_type, "STRING")
        properties[name] = genai_types.Schema(
            type=gemini_type,
            description=prop.get("description", ""),
        )

    params = genai_types.Schema(
        type="OBJECT",
        properties=properties,
        required=schema.get("required", []),
    ) if properties else None

    return genai_types.FunctionDeclaration(
        name=tool.name,
        description=tool.description or "",
        parameters=params,
    )


# ─────────────────────────── Client class ────────────────────────────────────

class RedshiftMCPClient:
    """Connects to the Redshift MCP server and exposes a Gemini chat loop."""

    # Path to server.py in the same directory as this client
    SERVER_SCRIPT = str(
        __import__("pathlib").Path(__file__).parent / "server.py"
    )

    def __init__(self) -> None:
        self.session: ClientSession | None = None
        self._exit_stack = AsyncExitStack()
        self.gemini: genai.Client | None = None
        self.tools: list[genai_types.Tool] = []
        self.mcp_tools: list[Tool] = []
        self.chat_history: list[genai_types.Content] = []

    # ── connect ──────────────────────────────────────────────────────────────

    async def connect(self) -> None:
        """Start the Redshift MCP server subprocess and open an MCP session."""
        print(f"{DIM}🔌 Connecting to Redshift MCP server …{RESET}", flush=True)

        server_params = StdioServerParameters(
            command=sys.executable,
            args=[self.SERVER_SCRIPT],
            env=None,
        )

        stdio_transport = await self._exit_stack.enter_async_context(
            stdio_client(server_params)
        )
        self.session = await self._exit_stack.enter_async_context(
            ClientSession(*stdio_transport)
        )

        await self.session.initialize()

        # Discover tools
        result = await self.session.list_tools()
        self.mcp_tools = result.tools

        print(f"{GREEN}✔ Connected!{RESET}  Server exposes {BOLD}{len(self.mcp_tools)}{RESET} tool(s):")
        for t in self.mcp_tools:
            print(f"  {YELLOW}•{RESET} {BOLD}{t.name}{RESET} — {DIM}{t.description}{RESET}")
        print()

        # Build Gemini tool declarations
        declarations = [mcp_tool_to_gemini_function(t) for t in self.mcp_tools]
        self.tools = [genai_types.Tool(function_declarations=declarations)]

    # ── Gemini setup ─────────────────────────────────────────────────────────

    def setup_gemini(self) -> None:
        api_key = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
        if not api_key:
            print(
                f"{RED}✖ GEMINI_API_KEY not found in environment.\n"
                f"  Export it and re-run: export GEMINI_API_KEY=your-key{RESET}"
            )
            sys.exit(1)

        self.gemini = genai.Client(api_key=api_key)
        print(f"{GREEN}✔ Gemini client ready.{RESET}\n")

    # ── tool call execution ───────────────────────────────────────────────────

    async def call_mcp_tool(self, name: str, args: dict[str, Any]) -> str:
        """Execute an MCP tool and return the text result."""
        assert self.session, "Not connected"
        result = await self.session.call_tool(name, args)
        parts = result.content
        texts = [p.text for p in parts if hasattr(p, "text")]
        return "\n".join(texts) if texts else "(no output)"

    # ── tool smoke-tests ─────────────────────────────────────────────────────

    async def run_smoke_tests(self) -> None:
        """
        Directly invoke each MCP tool to verify the server works end-to-end,
        without going through Gemini.
        """
        tool_names = [t.name for t in self.mcp_tools]
        step = 0
        total = len(tool_names)

        def label(name: str) -> str:
            nonlocal step
            step += 1
            return f"{YELLOW}[{step}/{total}]{RESET} {BOLD}{name}{RESET}"

        print(f"\n{BOLD}{BLUE}━━━ MCP Tool Smoke Tests ({total} tools) ━━━{RESET}")

        # ── get_allowed_schemas ───────────────────────────────────────────────
        if "get_allowed_schemas" in tool_names:
            print(f"\n{label('get_allowed_schemas')}()")
            result = await self.call_mcp_tool("get_allowed_schemas", {})
            print(f"  → {result}")

        # ── list_schemas ─────────────────────────────────────────────────────
        if "list_schemas" in tool_names:
            print(f"\n{label('list_schemas')}()")
            schemas_result = await self.call_mcp_tool("list_schemas", {})
            print(f"  → {schemas_result}")

        # Parse the default schema from get_allowed_schemas for subsequent tests
        default_schema = None
        if "get_allowed_schemas" in tool_names:
            raw = await self.call_mcp_tool("get_allowed_schemas", {})
            try:
                cfg = json.loads(raw)
                default_schema = cfg.get("default_schema")
            except (json.JSONDecodeError, TypeError):
                # Fallback: extract from string
                m = re.search(r"default_schema['\"]?\s*[:=]\s*['\"]?(\w+)", raw)
                if m:
                    default_schema = m.group(1)
        schema_arg = default_schema or "gold_capsaai"

        # ── list_tables ──────────────────────────────────────────────────────
        first_table = None
        if "list_tables" in tool_names:
            print(f"\n{label('list_tables')}(schema='{schema_arg}')")
            tables_result = await self.call_mcp_tool("list_tables", {"schema": schema_arg})
            print(f"  → {tables_result[:500]}{'…' if len(tables_result) > 500 else ''}")
            # list_tables returns newline-separated names or JSON array
            table_names = re.findall(r'"([^"]+)"', tables_result)
            if not table_names:
                # Fallback: plain newline-separated names
                table_names = [
                    line.strip() for line in tables_result.splitlines()
                    if line.strip() and not line.strip().startswith(("[", "]"))
                ]
            if table_names:
                first_table = table_names[0]

        # ── describe_table ───────────────────────────────────────────────────
        if "describe_table" in tool_names and first_table:
            print(f"\n{label('describe_table')}(table='{first_table}', schema='{schema_arg}')")
            result = await self.call_mcp_tool(
                "describe_table", {"table": first_table, "schema": schema_arg}
            )
            print(f"  → {result[:800]}{'…' if len(result) > 800 else ''}")
        elif "describe_table" in tool_names:
            print(f"\n{label('describe_table')} — {DIM}skipped (no tables){RESET}")

        # ── sample_data ──────────────────────────────────────────────────────
        if "sample_data" in tool_names and first_table:
            print(f"\n{label('sample_data')}(table='{first_table}', schema='{schema_arg}', limit=3)")
            result = await self.call_mcp_tool(
                "sample_data", {"table": first_table, "schema": schema_arg, "limit": 3}
            )
            print(f"  → {result[:800]}{'…' if len(result) > 800 else ''}")
        elif "sample_data" in tool_names:
            print(f"\n{label('sample_data')} — {DIM}skipped (no tables){RESET}")

        # ── table_row_count ──────────────────────────────────────────────────
        if "table_row_count" in tool_names and first_table:
            print(f"\n{label('table_row_count')}(table='{first_table}', schema='{schema_arg}')")
            result = await self.call_mcp_tool(
                "table_row_count", {"table": first_table, "schema": schema_arg}
            )
            print(f"  → {result}")
        elif "table_row_count" in tool_names:
            print(f"\n{label('table_row_count')} — {DIM}skipped (no tables){RESET}")

        # ── search_columns ───────────────────────────────────────────────────
        if "search_columns" in tool_names:
            print(f"\n{label('search_columns')}(keyword='id')")
            result = await self.call_mcp_tool("search_columns", {"keyword": "id"})
            # May return many results — truncate
            print(f"  → {result[:600]}{'…' if len(result) > 600 else ''}")

        # ── query_data ───────────────────────────────────────────────────────
        if "query_data" in tool_names and first_table:
            sql = f"SELECT * FROM {schema_arg}.{first_table} LIMIT 3"
            print(f"\n{label('query_data')}(sql='{sql}')")
            result = await self.call_mcp_tool("query_data", {"sql": sql})
            print(f"  → {result[:800]}{'…' if len(result) > 800 else ''}")
        elif "query_data" in tool_names:
            print(f"\n{label('query_data')} — {DIM}skipped (no tables){RESET}")

        # ── explain_query ────────────────────────────────────────────────────
        if "explain_query" in tool_names and first_table:
            sql = f"SELECT * FROM {schema_arg}.{first_table} LIMIT 5"
            print(f"\n{label('explain_query')}(sql='{sql}')")
            result = await self.call_mcp_tool("explain_query", {"sql": sql})
            print(f"  → {result[:800]}{'…' if len(result) > 800 else ''}")
        elif "explain_query" in tool_names:
            print(f"\n{label('explain_query')} — {DIM}skipped (no tables){RESET}")

        # ── export_to_csv ────────────────────────────────────────────────────
        if "export_to_csv" in tool_names and first_table:
            sql = f"SELECT * FROM {schema_arg}.{first_table} LIMIT 3"
            print(f"\n{label('export_to_csv')}(sql='{sql}')")
            result = await self.call_mcp_tool("export_to_csv", {"sql": sql})
            print(f"  → {result[:600]}{'…' if len(result) > 600 else ''}")
        elif "export_to_csv" in tool_names:
            print(f"\n{label('export_to_csv')} — {DIM}skipped (no tables){RESET}")

        # Any remaining tools not covered above
        tested = {
            "get_allowed_schemas", "list_schemas", "list_tables", "describe_table",
            "sample_data", "table_row_count", "search_columns", "query_data",
            "explain_query", "export_to_csv",
        }
        for name in tool_names:
            if name not in tested:
                print(f"\n{label(name)} — {DIM}not covered by smoke tests{RESET}")

        print(f"\n{GREEN}{BOLD}✔ Smoke tests complete.{RESET}\n")

    # ── chat round ───────────────────────────────────────────────────────────

    async def chat(self, user_input: str) -> str:
        """
        Send user_input to Gemini, handle tool calls transparently,
        and return the final text reply.
        """
        assert self.gemini, "Gemini not set up"

        # Append user turn
        self.chat_history.append(
            genai_types.Content(role="user", parts=[genai_types.Part(text=user_input)])
        )

        config = genai_types.GenerateContentConfig(
            tools=self.tools,
            system_instruction=(
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
            ),
        )

        # Agentic loop: keep calling Gemini until no more function calls
        while True:
            response = self.gemini.models.generate_content(
                model="gemini-2.5-flash",
                contents=self.chat_history,
                config=config,
            )

            candidate = response.candidates[0]
            content = candidate.content

            # Append model turn to history
            self.chat_history.append(content)

            # Check for function calls
            function_calls = [p for p in content.parts if p.function_call]
            if not function_calls:
                text_parts = [p.text for p in content.parts if p.text]
                return "\n".join(text_parts)

            # Execute each tool call and collect results
            tool_response_parts: list[genai_types.Part] = []
            for fc_part in function_calls:
                fc = fc_part.function_call
                tool_name = fc.name
                tool_args = dict(fc.args) if fc.args else {}

                print(
                    f"  {MAGENTA}⚙  Calling tool {BOLD}{tool_name}{RESET}{MAGENTA} "
                    f"with {tool_args}{RESET}",
                    flush=True,
                )

                tool_result = await self.call_mcp_tool(tool_name, tool_args)

                tool_response_parts.append(
                    genai_types.Part(
                        function_response=genai_types.FunctionResponse(
                            name=tool_name,
                            response={"result": tool_result},
                        )
                    )
                )

            # Append tool results as a "user" turn (Gemini convention)
            self.chat_history.append(
                genai_types.Content(role="user", parts=tool_response_parts)
            )
            # Loop: let Gemini process tool results

    # ── main loop ────────────────────────────────────────────────────────────

    async def run(self) -> None:
        banner()
        self.setup_gemini()
        await self.connect()

        # Run direct smoke tests before entering chat loop
        await self.run_smoke_tests()

        while True:
            try:
                user_input = input(f"{CYAN}{BOLD}You ▶{RESET} ").strip()
            except (EOFError, KeyboardInterrupt):
                print(f"\n{DIM}Goodbye!{RESET}")
                break

            if not user_input:
                continue

            if user_input.lower() in {"exit", "quit", "q"}:
                print(f"{DIM}Goodbye!{RESET}")
                break

            if user_input.lower() == "tools":
                print(f"\n{BOLD}Available MCP tools:{RESET}")
                for t in self.mcp_tools:
                    print(f"  {YELLOW}•{RESET} {BOLD}{t.name}{RESET} — {t.description}")
                print()
                continue

            print(f"{DIM}Thinking …{RESET}", flush=True)
            try:
                reply = await self.chat(user_input)
                print(f"\n{GREEN}{BOLD}Assistant ▶{RESET}\n{reply}\n")
            except Exception as exc:  # noqa: BLE001
                print(f"{RED}Error: {exc}{RESET}\n")

    # ── cleanup ──────────────────────────────────────────────────────────────

    async def close(self) -> None:
        await self._exit_stack.aclose()


# ─────────────────────────── entry point ─────────────────────────────────────

async def main() -> None:
    client = RedshiftMCPClient()
    try:
        await client.run()
    finally:
        await client.close()


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8")
    asyncio.run(main())
