#!/bin/bash
# Clean restart script for Claude Desktop MCP server

echo "🔄 Restarting Claude Desktop MCP server..."

# Kill any existing Python server processes
echo "🛑 Stopping existing server processes..."
pkill -f "/Users/parivallal/workspace/claud/server.py" 2>/dev/null || true

# Kill any SSH tunnels to our bastion
echo "🔒 Cleaning up SSH tunnels..."
pkill -f "ssh.*${SSH_HOST:-your-bastion-host}" 2>/dev/null || true

# Wait a moment for cleanup
sleep 2

# Check if port 5439 is free
if lsof -ti :5439 >/dev/null 2>&1; then
    echo "⚠️  Port 5439 still in use, attempting to free it..."
    lsof -ti :5439 | xargs kill -9 2>/dev/null || true
    sleep 2
fi

# Test SSH connection
echo "🔗 Testing SSH connection..."
if ssh -i ${SSH_KEY_FILE:-~/.ssh/id_rsa} -o ConnectTimeout=10 ${SSH_USER:-access}@${SSH_HOST:-your-bastion-host} "echo 'OK'" >/dev/null 2>&1; then
    echo "✅ SSH connection test passed"
else
    echo "❌ SSH connection test failed"
    exit 1
fi

echo "✅ Cleanup complete!"
echo ""
echo "📱 Now restart Claude Desktop:"
echo "  1. Quit Claude Desktop completely (Cmd+Q)"
echo "  2. Reopen Claude Desktop"
echo "  3. Try: 'Show me all tables in my Redshift database'"