#!/usr/bin/env python3
"""
Test script to verify the MCP server setup.
Run this after setting up your .env file with Redshift credentials.
"""

import os
import sys
from dotenv import load_dotenv
import psycopg2
from sshtunnel import SSHTunnelForwarder
import paramiko

def test_connection():
    """Test the Redshift connection without starting the MCP server."""
    
    # Load environment variables
    load_dotenv()
    
    required_vars = ["RS_HOST", "RS_DB", "RS_USER", "RS_PASS"]
    missing_vars = [var for var in required_vars if not os.environ.get(var)]
    
    if missing_vars:
        print(f"❌ Missing environment variables: {', '.join(missing_vars)}")
        print("Please edit your .env file with the correct Redshift credentials.")
        return False
    
    ssh_tunnel = None
    try:
        # Setup SSH tunnel if enabled
        if os.environ.get("SSH_TUNNEL", "false").lower() == "true":
            print("🔗 Setting up SSH tunnel...")
            
            ssh_host = os.environ.get("SSH_HOST")
            ssh_port = int(os.environ.get("SSH_PORT", "22"))
            ssh_user = os.environ.get("SSH_USER")
            ssh_key_file = os.environ.get("SSH_KEY_FILE")
            ssh_password = os.environ.get("SSH_PASSWORD")
            local_port = int(os.environ.get("LOCAL_PORT", "5439"))
            
            if not all([ssh_host, ssh_user]):
                print("❌ SSH_HOST and SSH_USER are required when SSH_TUNNEL=true")
                return False
            
            # Prepare SSH authentication
            ssh_auth = {}
            if ssh_key_file and os.path.exists(os.path.expanduser(ssh_key_file)):
                key_path = os.path.expanduser(ssh_key_file)
                try:
                    # Try to load the SSH key using paramiko
                    pkey = paramiko.RSAKey.from_private_key_file(key_path)
                    ssh_auth["ssh_pkey"] = pkey
                    print(f"🔑 Using RSA SSH key: {ssh_key_file}")
                except paramiko.ssh_exception.PasswordRequiredException:
                    print("❌ SSH key requires a passphrase. Please use SSH_PASSWORD for the passphrase.")
                    return False
                except Exception as e:
                    try:
                        # Try Ed25519 key format
                        pkey = paramiko.Ed25519Key.from_private_key_file(key_path)
                        ssh_auth["ssh_pkey"] = pkey
                        print(f"🔑 Using Ed25519 SSH key: {ssh_key_file}")
                    except Exception:
                        try:
                            # Try ECDSA key format
                            pkey = paramiko.ECDSAKey.from_private_key_file(key_path)
                            ssh_auth["ssh_pkey"] = pkey
                            print(f"🔑 Using ECDSA SSH key: {ssh_key_file}")
                        except Exception:
                            print(f"❌ Unsupported SSH key format: {e}")
                            return False
            elif ssh_password:
                ssh_auth["ssh_password"] = ssh_password
                print("🔑 Using SSH password authentication")
            else:
                print(f"❌ SSH key file not found: {ssh_key_file}")
                print("❌ Either SSH_KEY_FILE or SSH_PASSWORD is required for SSH tunnel")
                return False
            
            ssh_tunnel = SSHTunnelForwarder(
                (ssh_host, ssh_port),
                ssh_username=ssh_user,
                **ssh_auth,
                remote_bind_address=(os.environ["RS_HOST"], 5439),
                local_bind_address=("127.0.0.1", local_port)
            )
            
            ssh_tunnel.start()
            print(f"✅ SSH tunnel established on local port {ssh_tunnel.local_bind_port}")
            
            # Use tunnel for connection
            host, port = "127.0.0.1", ssh_tunnel.local_bind_port
            sslmode = "prefer"
        else:
            print("🔗 Connecting directly to Redshift...")
            host, port = os.environ["RS_HOST"], 5439
            sslmode = "require"
        
        print(f"🔄 Testing Redshift connection to {host}:{port}...")
        conn = psycopg2.connect(
            host=host,
            port=port,
            dbname=os.environ["RS_DB"],
            user=os.environ["RS_USER"],
            password=os.environ["RS_PASS"],
            sslmode=sslmode
        )
        
        # Test a simple query
        with conn.cursor() as cur:
            cur.execute("SELECT current_database(), current_user")
            db, user = cur.fetchone()
            print(f"✅ Successfully connected to database '{db}' as user '{user}'")
        
        conn.close()
        return True
        
    except Exception as e:
        print(f"❌ Connection failed: {str(e)}")
        return False
    finally:
        # Clean up SSH tunnel
        if ssh_tunnel:
            print("🔒 Closing SSH tunnel...")
            ssh_tunnel.stop()

def test_mcp_imports():
    """Test that MCP dependencies can be imported."""
    try:
        print("🔄 Testing MCP imports...")
        from mcp.server.fastmcp import FastMCP
        print("✅ FastMCP imported successfully")
        return True
    except ImportError as e:
        print(f"❌ Import failed: {str(e)}")
        print("Run: pip install -r requirements.txt")
        return False

if __name__ == "__main__":
    print("Redshift MCP Server Test\n" + "="*30)
    
    # Test imports first
    if not test_mcp_imports():
        sys.exit(1)
    
    # Test database connection
    if not test_connection():
        sys.exit(1)
    
    print("\n✅ All tests passed! Your MCP server should work correctly.")
    print("\nNext steps:")
    print("1. Run: python server.py")
    print("2. Configure Claude Desktop (see README.md)")