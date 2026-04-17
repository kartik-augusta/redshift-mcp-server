#!/usr/bin/env python3
"""
Test script to verify access to specific schemas, especially the gold schema.
"""

import os
import sys
from dotenv import load_dotenv
import psycopg2
from sshtunnel import SSHTunnelForwarder

def test_schema_access():
    """Test access to different schemas."""
    
    # Load environment variables
    load_dotenv()
    
    ssh_tunnel = None
    try:
        # Setup SSH tunnel
        ssh_host = os.environ.get("SSH_HOST")
        ssh_port = int(os.environ.get("SSH_PORT", "22"))
        ssh_user = os.environ.get("SSH_USER")
        ssh_key_file = os.environ.get("SSH_KEY_FILE")
        local_port = int(os.environ.get("LOCAL_PORT", "5439"))
        
        print("🔗 Setting up SSH tunnel...")
        ssh_tunnel = SSHTunnelForwarder(
            (ssh_host, ssh_port),
            ssh_username=ssh_user,
            ssh_pkey=os.path.expanduser(ssh_key_file),
            remote_bind_address=(os.environ["RS_HOST"], 5439),
            local_bind_address=("127.0.0.1", local_port)
        )
        
        ssh_tunnel.start()
        print(f"✅ SSH tunnel established on local port {ssh_tunnel.local_bind_port}")
        
        # Connect to Redshift
        conn = psycopg2.connect(
            host="127.0.0.1",
            port=ssh_tunnel.local_bind_port,
            dbname=os.environ["RS_DB"],
            user=os.environ["RS_USER"],
            password=os.environ["RS_PASS"],
            sslmode="prefer"
        )
        
        with conn.cursor() as cur:
            
            # Test 1: List all schemas user has access to
            print("\\n📋 Testing schema access...")
            cur.execute("SELECT schema_name FROM information_schema.schemata WHERE schema_name NOT IN ('information_schema', 'pg_catalog', 'pg_toast') ORDER BY schema_name")
            schemas = [r[0] for r in cur.fetchall()]
            print(f"✅ Accessible schemas: {', '.join(schemas)}")
            
            # Test 2: Check gold schema specifically
            if 'gold' in schemas:
                print("\\n🥇 Testing gold schema access...")
                cur.execute("SELECT tablename FROM pg_tables WHERE schemaname = 'gold' ORDER BY tablename")
                gold_tables = [r[0] for r in cur.fetchall()]
                
                if gold_tables:
                    print(f"✅ Gold schema tables ({len(gold_tables)}): {', '.join(gold_tables[:5])}{'...' if len(gold_tables) > 5 else ''}")
                    
                    # Test accessing a specific table in gold schema
                    test_table = gold_tables[0]
                    cur.execute(f"SELECT column_name FROM information_schema.columns WHERE table_schema = 'gold' AND table_name = %s LIMIT 5", (test_table,))
                    columns = [r[0] for r in cur.fetchall()]
                    print(f"✅ Sample table columns for gold.{test_table}: {', '.join(columns)}")
                else:
                    print("⚠️  Gold schema exists but contains no tables")
            else:
                print("❌ Gold schema not accessible")
            
            # Test 3: Check public schema access
            if 'public' in schemas:
                print("\\n🌐 Testing public schema access...")
                cur.execute("SELECT tablename FROM pg_tables WHERE schemaname = 'public' ORDER BY tablename LIMIT 5")
                public_tables = [r[0] for r in cur.fetchall()]
                if public_tables:
                    print(f"ℹ️  Public schema sample tables: {', '.join(public_tables)}")
                else:
                    print("ℹ️  Public schema has no tables or no access")
        
        conn.close()
        return True
        
    except Exception as e:
        print(f"❌ Schema access test failed: {str(e)}")
        return False
    finally:
        # Clean up SSH tunnel
        if ssh_tunnel:
            print("🔒 Closing SSH tunnel...")
            ssh_tunnel.stop()

if __name__ == "__main__":
    print("Gold Schema Access Test")
    print("="*30)
    
    if test_schema_access():
        print("\\n✅ Schema access tests completed successfully!")
    else:
        print("\\n❌ Schema access tests failed!")
        sys.exit(1)