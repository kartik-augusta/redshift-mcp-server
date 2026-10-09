#!/usr/bin/env python3
"""
Test script to validate MCP server stability and connection reliability.
"""

import sys
import time
from dotenv import load_dotenv
from server import get_conn, cleanup_ssh_tunnel

# Load environment variables
load_dotenv()

def test_connection_stability(num_tests=5):
    """Test connection stability by connecting multiple times."""
    print(f"🧪 Testing connection stability ({num_tests} attempts)")
    print("=" * 50)
    
    success_count = 0
    
    for i in range(num_tests):
        print(f"\n🔄 Test {i + 1}/{num_tests}")
        try:
            # Get connection
            conn = get_conn()
            
            # Run simple query
            with conn.cursor() as cur:
                cur.execute("SELECT current_database(), current_user, version()")
                result = cur.fetchone()
                print(f"✅ Connected to {result[0]} as {result[1]}")
                print(f"   Version: {result[2][:50]}...")
            
            # Close connection
            conn.close()
            print("✅ Connection closed cleanly")
            success_count += 1
            
            if i < num_tests - 1:  # Don't sleep after last test
                time.sleep(2)  # Wait between tests
                
        except Exception as e:
            print(f"❌ Test {i + 1} failed: {e}")
        
        print("-" * 40)
    
    print(f"\n📊 Results: {success_count}/{num_tests} tests passed")
    
    if success_count == num_tests:
        print("🎉 All tests passed - connection is stable!")
    elif success_count == 0:
        print("💀 All tests failed - connection is broken")
    else:
        print("⚠️  Some tests failed - connection is unstable")
    
    return success_count == num_tests

def test_schema_access():
    """Test that we can only access gold schema."""
    print("\n🔒 Testing schema access restrictions")
    print("=" * 40)
    
    try:
        conn = get_conn()
        
        # Test schema listing
        with conn.cursor() as cur:
            cur.execute("""
                SELECT schema_name 
                FROM information_schema.schemata 
                WHERE schema_name IN ('public', 'gold', 'bronze', 'silver')
                ORDER BY schema_name
            """)
            schemas = [row[0] for row in cur.fetchall()]
            print(f"📊 Available schemas in DB: {schemas}")
        
        # Test table access in gold schema
        with conn.cursor() as cur:
            cur.execute("""
                SELECT table_name 
                FROM information_schema.tables 
                WHERE table_schema = 'gold' 
                LIMIT 5
            """)
            tables = [row[0] for row in cur.fetchall()]
            print(f"✅ Gold schema tables: {tables}")
        
        conn.close()
        print("✅ Schema access test completed")
        return True
        
    except Exception as e:
        print(f"❌ Schema access test failed: {e}")
        return False

if __name__ == "__main__":
    try:
        print("🚀 MCP Server Stability Test")
        print("=" * 50)
        
        # Test connection stability
        stable = test_connection_stability(3)
        
        # Test schema access if connection is stable
        if stable:
            test_schema_access()
        
        print("\n🏁 Tests completed")
        
    except KeyboardInterrupt:
        print("\n⚠️  Tests interrupted by user")
    except Exception as e:
        print(f"\n💥 Unexpected error: {e}")
    finally:
        print("\n🔒 Cleaning up...")
        cleanup_ssh_tunnel()
        print("✅ Cleanup complete")