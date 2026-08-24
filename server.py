"""
Redshift MCP Server — read-only tools for exploring and querying a Redshift warehouse.

All tunables (schemas, limits, timeouts) are read from config.py / environment variables.
"""

from mcp.server.fastmcp import FastMCP
import psycopg2
import psycopg2.errors
import re
import csv
import io
import os
from sshtunnel import SSHTunnelForwarder
import atexit
import socket
import time
import signal
import sys

import config

# ─────────────────────────── Global state ────────────────────────────────────

ssh_tunnel = None       # Global SSH tunnel instance
_cached_conn = None     # Cached database connection

mcp = FastMCP("redshift-mcp")

# ─────────────────────────── SSH / networking ────────────────────────────────


def is_port_in_use(port: int) -> bool:
    """Check if a port is in use."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        try:
            s.bind(("127.0.0.1", port))
            return False
        except socket.error:
            return True


def find_available_port(start_port: int, max_port: int) -> int:
    """Find an available port starting from start_port."""
    for port in range(start_port, max_port + 1):
        if not is_port_in_use(port):
            return port
    raise Exception(f"No available ports found in range {start_port}-{max_port}")


def setup_ssh_tunnel():
    """Set up SSH tunnel if enabled in environment variables."""
    global ssh_tunnel

    if config.SSH_TUNNEL_ENABLED:
        if not all([config.SSH_HOST, config.SSH_USER]):
            raise ValueError("SSH_HOST and SSH_USER are required when SSH_TUNNEL=true")

        # Check if tunnel already exists and is active
        if ssh_tunnel and ssh_tunnel.is_active:
            print(f"✅ Using existing SSH tunnel on port {ssh_tunnel.local_bind_port}")
            return "127.0.0.1", ssh_tunnel.local_bind_port

        local_port = config.LOCAL_PORT

        # Find available port if default is in use
        if is_port_in_use(local_port):
            print(f"⚠️  Port {local_port} is in use, finding alternative...")
            local_port = find_available_port(
                local_port, local_port + config.PORT_SCAN_RANGE
            )
            print(f"🔄 Using port {local_port}")

        # Prepare SSH authentication
        ssh_auth = {}
        if config.SSH_KEY_FILE and os.path.exists(
            os.path.expanduser(config.SSH_KEY_FILE)
        ):
            ssh_auth["ssh_pkey"] = os.path.expanduser(config.SSH_KEY_FILE)
        elif config.SSH_PASSWORD:
            ssh_auth["ssh_password"] = config.SSH_PASSWORD
        else:
            raise ValueError(
                "Either SSH_KEY_FILE or SSH_PASSWORD is required for SSH tunnel"
            )

        print(f"🔗 Setting up SSH tunnel to {config.SSH_HOST}:{config.SSH_PORT}")

        for attempt in range(config.MAX_RETRIES):
            try:
                ssh_tunnel = SSHTunnelForwarder(
                    (config.SSH_HOST, config.SSH_PORT),
                    ssh_username=config.SSH_USER,
                    **ssh_auth,
                    remote_bind_address=(config.RS_HOST, config.RS_PORT),
                    local_bind_address=("127.0.0.1", local_port),
                    set_keepalive=config.SSH_KEEPALIVE,
                    allow_agent=False,
                    compression=True,
                )

                print(
                    f"🔄 Attempt {attempt + 1}/{config.MAX_RETRIES} - Starting SSH tunnel..."
                )
                ssh_tunnel.start()

                # Test the tunnel
                for _ in range(config.CONNECT_TIMEOUT):
                    time.sleep(1)
                    if ssh_tunnel.is_active:
                        break
                else:
                    raise Exception(
                        f"SSH tunnel failed to become active within {config.CONNECT_TIMEOUT} seconds"
                    )

                print(
                    f"✅ SSH tunnel established on local port {ssh_tunnel.local_bind_port}"
                )

                atexit.register(cleanup_ssh_tunnel)
                signal.signal(signal.SIGTERM, signal_handler)
                signal.signal(signal.SIGINT, signal_handler)

                return "127.0.0.1", ssh_tunnel.local_bind_port

            except Exception as e:
                print(f"❌ SSH tunnel attempt {attempt + 1} failed: {e}")
                if ssh_tunnel:
                    try:
                        ssh_tunnel.stop()
                    except Exception:
                        pass
                    ssh_tunnel = None

                if attempt < config.MAX_RETRIES - 1:
                    wait_time = 2**attempt
                    print(f"🔄 Retrying in {wait_time} seconds...")
                    time.sleep(wait_time)
                else:
                    print(f"❌ All {config.MAX_RETRIES} SSH tunnel attempts failed")
                    raise Exception(
                        f"SSH tunnel failed after {config.MAX_RETRIES} attempts: {e}"
                    )
    else:
        return config.RS_HOST, config.RS_PORT


def signal_handler(signum, frame):
    """Handle signals for graceful shutdown."""
    print(f"🛑 Received signal {signum}, cleaning up...")
    cleanup_ssh_tunnel()
    sys.exit(0)


def cleanup_ssh_tunnel():
    """Clean up SSH tunnel on exit."""
    global ssh_tunnel
    if ssh_tunnel:
        print("🔒 Closing SSH tunnel...")
        try:
            ssh_tunnel.stop()
        except Exception as e:
            print(f"⚠️  Error stopping tunnel: {e}")
        finally:
            ssh_tunnel = None


# ─────────────────────── Connection management ───────────────────────────────


def get_conn():
    """
    Return a cached database connection, reconnecting only when stale.

    Keeps a single long-lived connection open and validates it with a
    lightweight health check before returning. If the check fails the
    connection is discarded and a fresh one is established with retry logic.
    """
    global _cached_conn

    # Fast path: reuse existing healthy connection
    if _cached_conn is not None:
        try:
            with _cached_conn.cursor() as cur:
                cur.execute("SELECT 1")
                cur.fetchone()
            return _cached_conn
        except Exception:
            # Connection is stale — close and fall through to reconnect
            try:
                _cached_conn.close()
            except Exception:
                pass
            _cached_conn = None

    # Slow path: establish a new connection with retries
    for attempt in range(config.MAX_RETRIES):
        try:
            host, port = setup_ssh_tunnel()

            print(
                f"🔄 Connecting to database (attempt {attempt + 1}/{config.MAX_RETRIES})..."
            )
            conn = psycopg2.connect(
                host=host,
                port=port,
                dbname=config.RS_DB,
                user=config.RS_USER,
                password=config.RS_PASS,
                sslmode="require" if host != "127.0.0.1" else "prefer",
                connect_timeout=config.CONNECT_TIMEOUT,
                application_name="MCP_Redshift_Server",
            )

            # Validate connection
            with conn.cursor() as cur:
                cur.execute("SELECT 1")
                cur.fetchone()

            print("✅ Database connection established")
            _cached_conn = conn
            return _cached_conn

        except Exception as e:
            print(f"❌ Database connection attempt {attempt + 1} failed: {e}")

            if attempt == config.MAX_RETRIES - 1:
                cleanup_ssh_tunnel()
                raise Exception(
                    f"Database connection failed after {config.MAX_RETRIES} attempts: {e}"
                )
            else:
                wait_time = 2**attempt
                print(f"🔄 Retrying in {wait_time} seconds...")
                time.sleep(wait_time)


# ─────────────────────── Validation helpers ──────────────────────────────────


def _validate_schema(schema: str) -> str:
    """Validate and normalise a schema name against the allowlist."""
    schema = (schema or config.DEFAULT_SCHEMA).strip().lower()
    if schema not in config.ALLOWED_SCHEMAS:
        raise ValueError(
            f"Schema '{schema}' is not in the allowed list: {config.ALLOWED_SCHEMAS}"
        )
    return schema


def _validate_read_only_sql(sql: str) -> str:
    """Ensure SQL is a read-only SELECT (or SET search_path). Returns stripped SQL."""
    sql = sql.strip()
    upper = sql.upper()
    if not upper.startswith("SELECT") and not upper.startswith("SET SEARCH_PATH"):
        raise ValueError("Only SELECT queries and SET search_path commands are allowed.")
    return sql


def _check_schema_references(sql: str) -> None:
    """Reject SQL that references schemas outside the allowlist."""
    upper = sql.upper()
    if "FROM " not in upper and "JOIN " not in upper:
        return
    schema_table_pattern = r"\b(\w+)\.(\w+)\b"
    for schema, table in re.findall(schema_table_pattern, sql):
        if schema.lower() not in config.ALLOWED_SCHEMAS:
            raise ValueError(
                f"Access restricted to schemas {config.ALLOWED_SCHEMAS}. "
                f"Cannot query '{schema}.{table}'."
            )


def _search_path_sql() -> str:
    """Return a SET search_path statement for all allowed schemas."""
    return f"SET search_path = {', '.join(config.ALLOWED_SCHEMAS)}"


def _apply_limit(sql: str, max_rows: int) -> str:
    """
    Ensure a SELECT query has a row cap.

    If the query already contains a LIMIT clause, wrap it in a subquery to
    enforce the server's maximum.  Otherwise, append LIMIT directly.
    """
    # Remove trailing semicolons and whitespace so appending LIMIT or wrapping works
    sql = sql.strip().rstrip(";")
    
    if re.search(r"\bLIMIT\b", sql, re.IGNORECASE):
        return f"SELECT * FROM ({sql}) _limited LIMIT {max_rows}"
    return f"{sql} LIMIT {max_rows}"


# ─────────────────────────── MCP Tools ───────────────────────────────────────

# ── Schema exploration ───────────────────────────────────────────────────────


@mcp.tool()
def list_schemas() -> list[str]:
    """List accessible schemas in the database (filtered by the configured allowlist)."""
    conn = get_conn()
    cur = conn.cursor()
    placeholders = ", ".join(["%s"] * len(config.ALLOWED_SCHEMAS))
    cur.execute(
        f"SELECT nspname FROM pg_namespace "
        f"WHERE LOWER(nspname) IN ({placeholders}) ORDER BY nspname",
        config.ALLOWED_SCHEMAS,
    )
    schemas = [r[0] for r in cur.fetchall()]
    return schemas


@mcp.tool()
def get_allowed_schemas() -> dict:
    """
    Return the server's schema access configuration: which schemas are in the
    allowlist, which is the default, and the current query limits.
    """
    return {
        "allowed_schemas": config.ALLOWED_SCHEMAS,
        "default_schema": config.DEFAULT_SCHEMA,
        "max_rows": config.MAX_ROWS,
        "max_export_rows": config.MAX_EXPORT_ROWS,
    }


# ── Table exploration ────────────────────────────────────────────────────────


@mcp.tool()
def list_tables(schema: str = "") -> list[str]:
    """List all tables in a schema. Only schemas in the allowlist are accessible."""
    schema = _validate_schema(schema)
    conn = get_conn()
    cur = conn.cursor()
    cur.execute(
        "SELECT tablename FROM pg_tables WHERE schemaname = %s ORDER BY tablename",
        (schema,),
    )
    tables = [r[0] for r in cur.fetchall()]
    if not tables:
        raise ValueError(f"No tables found in schema '{schema}' or access denied.")
    return tables


@mcp.tool()
def describe_table(table: str, schema: str = "") -> list[dict]:
    """Get column names, data types, nullability, and defaults for a table."""
    schema = _validate_schema(schema)
    conn = get_conn()
    cur = conn.cursor()
    cur.execute(
        """
        SELECT column_name, data_type, is_nullable, column_default
        FROM information_schema.columns
        WHERE table_schema = %s AND table_name = %s
        ORDER BY ordinal_position
        """,
        (schema, table),
    )
    columns = [
        {"column": r[0], "type": r[1], "nullable": r[2], "default": r[3]}
        for r in cur.fetchall()
    ]
    if not columns:
        raise ValueError(
            f"Table '{table}' not found in schema '{schema}' or access denied."
        )
    return columns


@mcp.tool()
def sample_data(table: str, schema: str = "", limit: int = 10) -> list[dict]:
    """
    Return a sample of rows from a table for quick data exploration.
    The limit is capped at the server's MAX_ROWS setting.
    """
    schema = _validate_schema(schema)
    capped_limit = min(max(1, limit), config.MAX_ROWS)
    conn = get_conn()
    cur = conn.cursor()
    try:
        cur.execute(_search_path_sql())
        cur.execute(
            f"SELECT * FROM {schema}.{table} LIMIT %s",
            (capped_limit,),
        )
        if cur.description:
            cols = [d[0] for d in cur.description]
            return [dict(zip(cols, row)) for row in cur.fetchall()]
        return []
    except Exception as e:
        raise ValueError(f"Sample query error: {e}")


@mcp.tool()
def table_row_count(table: str, schema: str = "") -> dict:
    """
    Get the row count for a table.
    Uses COUNT(*) for an exact count.
    """
    schema = _validate_schema(schema)
    conn = get_conn()
    cur = conn.cursor()
    try:
        cur.execute(_search_path_sql())
        cur.execute(f"SELECT COUNT(*) FROM {schema}.{table}")
        count = cur.fetchone()[0]
        return {"schema": schema, "table": table, "row_count": count}
    except Exception as e:
        raise ValueError(f"Row count error: {e}")


# ── Column search ────────────────────────────────────────────────────────────


@mcp.tool()
def search_columns(keyword: str, schema: str = "") -> list[dict]:
    """
    Search for columns whose name matches a keyword (case-insensitive) across
    all tables in one or all allowed schemas. Useful for discovering relevant data.
    """
    if schema:
        schemas_to_search = [_validate_schema(schema)]
    else:
        schemas_to_search = list(config.ALLOWED_SCHEMAS)

    conn = get_conn()
    cur = conn.cursor()
    placeholders = ", ".join(["%s"] * len(schemas_to_search))
    cur.execute(
        f"""
        SELECT table_schema, table_name, column_name, data_type
        FROM information_schema.columns
        WHERE LOWER(table_schema) IN ({placeholders})
          AND LOWER(column_name) LIKE %s
        ORDER BY table_schema, table_name, ordinal_position
        """,
        (*schemas_to_search, f"%{keyword.lower()}%"),
    )
    results = [
        {
            "schema": r[0],
            "table": r[1],
            "column": r[2],
            "type": r[3],
        }
        for r in cur.fetchall()
    ]
    return results


# ── Querying ─────────────────────────────────────────────────────────────────


@mcp.tool()
def query_data(sql: str) -> list[dict]:
    """
    Run a read-only SELECT query. Only schemas in the allowlist may be referenced.
    Results are capped at MAX_ROWS.
    """
    sql = _validate_read_only_sql(sql)
    _check_schema_references(sql)

    conn = get_conn()
    cur = conn.cursor()
    try:
        cur.execute(_search_path_sql())

        if sql.upper().startswith("SELECT"):
            cur.execute(_apply_limit(sql, config.MAX_ROWS))
        else:
            cur.execute(sql)

        if cur.description:
            cols = [d[0] for d in cur.description]
            return [dict(zip(cols, row)) for row in cur.fetchall()]
        else:
            return [{"status": "success", "message": "Command executed."}]
    except Exception as e:
        raise ValueError(f"Query error: {e}")


@mcp.tool()
def explain_query(sql: str) -> list[dict]:
    """
    Show the EXPLAIN plan for a SELECT query without executing it.
    Useful for understanding query performance before running.
    """
    sql = _validate_read_only_sql(sql)
    _check_schema_references(sql)

    if not sql.upper().startswith("SELECT"):
        raise ValueError("EXPLAIN is only supported for SELECT queries.")

    conn = get_conn()
    cur = conn.cursor()
    try:
        cur.execute(_search_path_sql())
        cur.execute(f"EXPLAIN {sql}")
        plan_lines = [r[0] for r in cur.fetchall()]
        return [{"plan": "\n".join(plan_lines)}]
    except Exception as e:
        raise ValueError(f"Explain error: {e}")


# ── Export ───────────────────────────────────────────────────────────────────


@mcp.tool()
def export_to_csv(sql: str) -> str:
    """
    Export query results to CSV format. Same security restrictions as query_data.
    Row limit is controlled by MAX_EXPORT_ROWS.
    """
    sql = _validate_read_only_sql(sql)
    _check_schema_references(sql)

    if not sql.upper().startswith("SELECT"):
        raise ValueError("Only SELECT queries can be exported to CSV.")

    conn = get_conn()
    cur = conn.cursor()
    try:
        cur.execute(_search_path_sql())
        cur.execute(_apply_limit(sql, config.MAX_EXPORT_ROWS))

        buffer = io.StringIO()
        writer = csv.writer(buffer)

        if cur.description:
            writer.writerow([desc[0] for desc in cur.description])
            for row in cur.fetchall():
                writer.writerow(row)

        buffer.seek(0)
        return buffer.getvalue()
    except Exception as e:
        raise ValueError(f"CSV export error: {e}")


# ─────────────────────────── Entry point ─────────────────────────────────────

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Redshift MCP Server")
    parser.add_argument(
        "--http",
        "--streamable-http",
        action="store_true",
        dest="http",
        help="Run in Streamable HTTP mode (/mcp) instead of stdio",
    )
    parser.add_argument(
        "--transport",
        choices=["stdio", "http", "streamable-http"],
        default=None,
        help="Transport protocol to use (default: stdio)",
    )
    parser.add_argument(
        "--host",
        default="0.0.0.0",
        help="Host to bind when using HTTP mode (default: 0.0.0.0)",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=8000,
        help="Port to bind when using HTTP mode (default: 8000)",
    )
    args = parser.parse_args()

    use_http = args.http or args.transport in ("http", "streamable-http")

    if use_http:
        print(f"🚀 Starting Redshift MCP server in Streamable HTTP mode on http://{args.host}:{args.port}/mcp")
        mcp._host = args.host
        mcp._port = args.port
        mcp.settings.host = args.host
        mcp.settings.port = args.port
        mcp.run(transport="streamable-http")
    else:
        mcp.run()