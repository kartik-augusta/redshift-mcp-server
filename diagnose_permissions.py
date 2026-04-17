#!/usr/bin/env python3
"""
Detailed diagnostic script to check user permissions and available objects.
"""

import os
import sys
from dotenv import load_dotenv
import psycopg2
from sshtunnel import SSHTunnelForwarder

def detailed_permissions_check():
    """Check detailed permissions and what the user can actually see."""
    
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
            
            # Check current user and database
            print("\\n🔍 Basic connection info...")
            cur.execute("SELECT current_user, current_database(), current_schema()")
            user, db, schema = cur.fetchone()
            print(f"✅ Connected as user: {user}")
            print(f"✅ Database: {db}")
            print(f"✅ Current schema: {schema}")
            
            # Check all schemas that exist (regardless of permissions)
            print("\\n🗂️  All schemas in database...")
            cur.execute("SELECT nspname FROM pg_namespace WHERE nspname NOT LIKE 'pg_%' AND nspname != 'information_schema' ORDER BY nspname")
            all_schemas = [r[0] for r in cur.fetchall()]
            print(f"📋 All schemas: {', '.join(all_schemas)}")
            
            # Check schemas with USAGE permission
            print("\\n🔐 Permission check...")
            cur.execute("""
                SELECT schema_name 
                FROM information_schema.schemata 
                WHERE schema_name NOT IN ('information_schema', 'pg_catalog', 'pg_toast')
                ORDER BY schema_name
            """)
            accessible_schemas = [r[0] for r in cur.fetchall()]
            print(f"✅ Schemas with USAGE permission: {', '.join(accessible_schemas) if accessible_schemas else 'None'}")
            
            # Check current search_path
            cur.execute("SHOW search_path")
            search_path = cur.fetchone()[0]
            print(f"🔍 Current search_path: {search_path}")
            
            # Try to list tables in each schema specifically
            print("\\n📊 Table access test...")
            for schema_name in ['public', 'gold'] + all_schemas[:3]:  # Test key schemas
                try:
                    cur.execute("SELECT tablename FROM pg_tables WHERE schemaname = %s LIMIT 5", (schema_name,))
                    tables = [r[0] for r in cur.fetchall()]
                    if tables:
                        print(f"  ✅ {schema_name}: {len(tables)} tables accessible (sample: {', '.join(tables[:3])})")
                    else:
                        print(f"  ⚠️  {schema_name}: No tables found or accessible")
                except Exception as e:
                    print(f"  ❌ {schema_name}: Error - {str(e)}")
                    
            # Check user's specific grants
            print("\\n🎫 User grants check...")
            try:
                cur.execute("""
                    SELECT 
                        grantee, 
                        table_schema, 
                        privilege_type 
                    FROM information_schema.schema_privileges 
                    WHERE grantee = current_user
                    ORDER BY table_schema, privilege_type
                """)
                grants = cur.fetchall()
                if grants:
                    for grantee, schema_name, privilege in grants:
                        print(f"  ✅ {privilege} on schema {schema_name}")
                else:
                    print("  ⚠️  No explicit schema privileges found for current user")
            except Exception as e:
                print(f"  ❌ Error checking grants: {str(e)}")
                
        conn.close()
        return True
        
    except Exception as e:
        print(f"❌ Diagnostic failed: {str(e)}")
        return False
    finally:
        # Clean up SSH tunnel
        if ssh_tunnel:
            print("🔒 Closing SSH tunnel...")
            ssh_tunnel.stop()

if __name__ == "__main__":
    print("Redshift Permissions Diagnostic")
    print("="*40)
    detailed_permissions_check()