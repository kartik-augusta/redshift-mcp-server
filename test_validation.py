#!/usr/bin/env python3
"""
Simple validation test that doesn't require database connection.
"""

import os
import sys
from dotenv import load_dotenv

def test_environment_config():
    """Test environment configuration."""
    print("🔒 Testing environment configuration...")
    
    load_dotenv()
    
    # Check required environment variables
    required_vars = ['RS_HOST', 'RS_DB', 'RS_USER', 'RS_PASS']
    missing_vars = []
    for var in required_vars:
        if not os.environ.get(var):
            missing_vars.append(var)

    if missing_vars:
        print(f'❌ Missing required environment variables: {missing_vars}')
        return False
    else:
        print('✅ All required environment variables are set')

    # Check SSH configuration if enabled
    if os.environ.get('SSH_TUNNEL', '').lower() == 'true':
        ssh_vars = ['SSH_HOST', 'SSH_USER']
        missing_ssh = []
        for var in ssh_vars:
            if not os.environ.get(var):
                missing_ssh.append(var)
        
        if missing_ssh:
            print(f'⚠️  SSH tunnel enabled but missing: {missing_ssh}')
            return False
        else:
            print('✅ SSH tunnel configuration looks complete')
            
            # Check SSH key file
            key_file = os.environ.get('SSH_KEY_FILE')
            if key_file and os.path.exists(key_file):
                print('✅ SSH key file exists')
            elif key_file:
                print('❌ SSH key file specified but not found')
                return False
            else:
                print('ℹ️  Using SSH password or default key')
                
    return True

def test_code_imports():
    """Test that all required modules can be imported."""
    print("\n📦 Testing code imports...")
    
    try:
        import server
        print("✅ server.py imports successfully")
        
        # Check if required functions exist
        required_functions = ['setup_ssh_tunnel', 'cleanup_ssh_tunnel', 'is_port_in_use', 'find_available_port']
        for func_name in required_functions:
            if hasattr(server, func_name):
                print(f'✅ Function {func_name} exists')
            else:
                print(f'❌ Function {func_name} missing')
                return False

        # Check if mcp instance exists
        if hasattr(server, 'mcp'):
            print('✅ FastMCP instance created')
        else:
            print('❌ FastMCP instance missing')
            return False
            
        return True
    except Exception as e:
        print(f"❌ Import failed: {e}")
        return False

def test_file_structure():
    """Test that all required files exist."""
    print("\n📁 Testing file structure...")
    
    required_files = [
        'server.py',
        'requirements.txt',
        '.env',
        'README.md'
    ]
    
    for file in required_files:
        if os.path.exists(file):
            print(f"✅ {file} exists")
        else:
            print(f"❌ {file} missing")
            return False
            
    return True

if __name__ == "__main__":
    print("Redshift MCP Server - Validation Tests")
    print("=" * 50)
    
    tests = [
        test_file_structure,
        test_environment_config,
        test_code_imports
    ]
    
    passed = 0
    total = len(tests)
    
    for test in tests:
        if test():
            passed += 1
        else:
            print(f"❌ {test.__name__} failed")
            
    print(f"\n📊 Results: {passed}/{total} validation tests passed")
    
    if passed == total:
        print("🎉 All validation tests passed! Code is ready for upload.")
        sys.exit(0)
    else:
        print("💥 Some validation tests failed. Please fix issues before uploading.")
        sys.exit(1)