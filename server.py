from mcp.server.fastmcp import FastMCP
import psycopg2
import psycopg2.errors
import os
from dotenv import load_dotenv
from sshtunnel import SSHTunnelForwarder
import atexit
import socket
import time
import signal
import sys

# Load environment variables from .env file
load_dotenv()

# Global SSH tunnel instance
ssh_tunnel = None

mcp = FastMCP("redshift-mcp")

def is_port_in_use(port):
    """Check if a port is in use."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        try:
            s.bind(("127.0.0.1", port))
            return False
        except socket.error:
            return True

def find_available_port(start_port=5439, max_port=5449):
    """Find an available port starting from start_port."""
    for port in range(start_port, max_port + 1):
        if not is_port_in_use(port):
            return port
    raise Exception(f"No available ports found in range {start_port}-{max_port}")

def setup_ssh_tunnel():
    """Set up SSH tunnel if enabled in environment variables."""
    global ssh_tunnel
    
    if os.environ.get("SSH_TUNNEL", "false").lower() == "true":
        ssh_host = os.environ.get("SSH_HOST")
        ssh_port = int(os.environ.get("SSH_PORT", "22"))
        ssh_user = os.environ.get("SSH_USER")
        ssh_key_file = os.environ.get("SSH_KEY_FILE")
        ssh_password = os.environ.get("SSH_PASSWORD")
        local_port = int(os.environ.get("LOCAL_PORT", "5439"))
        
        if not all([ssh_host, ssh_user]):
            raise ValueError("SSH_HOST and SSH_USER are required when SSH_TUNNEL=true")
        
        # Check if tunnel already exists and is active
        if ssh_tunnel and ssh_tunnel.is_active:
            print(f"✅ Using existing SSH tunnel on port {ssh_tunnel.local_bind_port}")
            return "127.0.0.1", ssh_tunnel.local_bind_port
        
        # Find available port if default is in use
        if is_port_in_use(local_port):
            print(f"⚠️  Port {local_port} is in use, finding alternative...")
            local_port = find_available_port(local_port, local_port + 10)
            print(f"🔄 Using port {local_port}")
        
        # Prepare SSH authentication
        ssh_auth = {}
        if ssh_key_file and os.path.exists(os.path.expanduser(ssh_key_file)):
            ssh_auth["ssh_pkey"] = os.path.expanduser(ssh_key_file)
        elif ssh_password:
            ssh_auth["ssh_password"] = ssh_password
        else:
            raise ValueError("Either SSH_KEY_FILE or SSH_PASSWORD is required for SSH tunnel")
        
        print(f"🔗 Setting up SSH tunnel to {ssh_host}:{ssh_port}")
        
        # Retry logic for tunnel establishment
        max_retries = 3
        for attempt in range(max_retries):
            try:
                ssh_tunnel = SSHTunnelForwarder(
                    (ssh_host, ssh_port),
                    ssh_username=ssh_user,
                    **ssh_auth,
                    remote_bind_address=(os.environ["RS_HOST"], 5439),
                    local_bind_address=("127.0.0.1", local_port),
                    set_keepalive=20.0,  # Keep connection alive every 20s
                    allow_agent=False,   # Don't use SSH agent
                    compression=True     # Enable compression for stability
                )
                
                print(f"🔄 Attempt {attempt + 1}/{max_retries} - Starting SSH tunnel...")
                ssh_tunnel.start()
                
                # Test the tunnel with longer wait
                for i in range(10):  # Wait up to 10 seconds
                    time.sleep(1)
                    if ssh_tunnel.is_active:
                        break
                else:
                    raise Exception("SSH tunnel failed to become active within 10 seconds")
                    
                print(f"✅ SSH tunnel established on local port {ssh_tunnel.local_bind_port}")
                
                # Register cleanup function
                atexit.register(cleanup_ssh_tunnel)
                signal.signal(signal.SIGTERM, signal_handler)
                signal.signal(signal.SIGINT, signal_handler)
                
                return "127.0.0.1", ssh_tunnel.local_bind_port
                
            except Exception as e:
                print(f"❌ SSH tunnel attempt {attempt + 1} failed: {e}")
                if ssh_tunnel:
                    try:
                        ssh_tunnel.stop()
                    except:
                        pass
                    ssh_tunnel = None
                
                if attempt < max_retries - 1:
                    wait_time = 2 ** attempt  # Exponential backoff
                    print(f"🔄 Retrying in {wait_time} seconds...")
                    time.sleep(wait_time)
                else:
                    print(f"❌ All {max_retries} SSH tunnel attempts failed")
                    raise Exception(f"SSH tunnel failed after {max_retries} attempts: {e}")
    else:
        return os.environ["RS_HOST"], 5439

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

def get_conn():
    """Get database connection with retry logic."""
    max_retries = 3
    for attempt in range(max_retries):
        try:
            host, port = setup_ssh_tunnel()
            
            print(f"🔄 Connecting to database (attempt {attempt + 1}/{max_retries})...")
            conn = psycopg2.connect(
                host=host,
                port=port,
                dbname=os.environ["RS_DB"],
                user=os.environ["RS_USER"],      # read-only user
                password=os.environ["RS_PASS"],
                sslmode="require" if host != "127.0.0.1" else "prefer",
                connect_timeout=15,  # Increase connection timeout
                application_name="MCP_Redshift_Server"
            )
            
            # Test the connection
            with conn.cursor() as cur:
                cur.execute("SELECT 1")
                cur.fetchone()
            
            print("✅ Database connection established")
            return conn
            
        except Exception as e:
            print(f"❌ Database connection attempt {attempt + 1} failed: {e}")
            
            # Clean up SSH tunnel on connection failure
            if attempt == max_retries - 1:  # Last attempt
                cleanup_ssh_tunnel()
                raise Exception(f"Database connection failed after {max_retries} attempts: {e}")
            else:
                wait_time = 2 ** attempt  # Exponential backoff
                print(f"🔄 Retrying in {wait_time} seconds...")
                time.sleep(wait_time)

@mcp.tool()
def list_schemas() -> list[str]:
    """List schemas the user should access - restricted to gold schema only for security."""
    # Return only gold schema to restrict access as intended
    return ["gold"]

@mcp.tool()
def list_tables(schema: str = "gold") -> list[str]:
    """List all tables in the gold schema. Access restricted to gold schema only."""
    # Enforce gold schema access only for security
    if schema.lower() != "gold":
        raise ValueError(f"Access restricted to 'gold' schema only. Requested schema '{schema}' is not allowed.")
    
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute(
            "SELECT tablename FROM pg_tables WHERE schemaname = 'gold' ORDER BY tablename"
        )
        tables = [r[0] for r in cur.fetchall()]
        if not tables:
            raise ValueError("No tables found in gold schema or access denied.")
        return tables

@mcp.tool()
def describe_table(table: str, schema: str = "gold") -> list[dict]:
    """Get columns and types for a table. Access restricted to gold schema only."""
    # Enforce gold schema access only for security
    if schema.lower() != "gold":
        raise ValueError(f"Access restricted to 'gold' schema only. Requested schema '{schema}' is not allowed.")
    
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute("""
            SELECT column_name, data_type, is_nullable, column_default
            FROM information_schema.columns
            WHERE table_schema = 'gold' AND table_name = %s
            ORDER BY ordinal_position
        """, (table,))
        columns = [{
            "column": r[0], 
            "type": r[1],
            "nullable": r[2],
            "default": r[3]
        } for r in cur.fetchall()]
        if not columns:
            raise ValueError(f"Table '{table}' not found in gold schema or access denied.")
        return columns

@mcp.tool()
def query_data(sql: str) -> list[dict]:
    """Run a SELECT query restricted to gold schema only. Read-only. Max 500 rows returned."""
    sql = sql.strip()
    if not sql.upper().startswith("SELECT") and not sql.upper().startswith("SET SEARCH_PATH"):
        raise ValueError("Only SELECT queries and SET search_path commands are allowed.")
    
    # Check for attempts to access other schemas
    sql_upper = sql.upper()
    if "FROM " in sql_upper:
        # Extract table references and check for schema qualification
        import re
        # Look for schema.table patterns
        schema_table_pattern = r'\b(\w+)\.(\w+)\b'
        matches = re.findall(schema_table_pattern, sql)
        for schema, table in matches:
            if schema.lower() != "gold":
                raise ValueError(f"Access restricted to 'gold' schema only. Cannot query '{schema}.{table}'.")
    
    with get_conn() as conn:
        cur = conn.cursor()
        try:
            # Set search path to gold schema only
            cur.execute("SET search_path = gold")
            
            cur.execute(sql + (" LIMIT 500" if sql.upper().startswith("SELECT") else ""))
            
            if cur.description:  # Query returned results
                cols = [d[0] for d in cur.description]
                return [dict(zip(cols, row)) for row in cur.fetchall()]
            else:  # SET command or similar
                return [{"status": "success", "message": "Search path set to gold schema"}]
        except Exception as e:
            raise ValueError(f"Query error: {str(e)}")

if __name__ == "__main__":
    mcp.run()