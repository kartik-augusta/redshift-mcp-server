# Quick Reference - Redshift MCP Server (Gold Schema Only)

## 🔒 Security Configuration
**IMPORTANT**: MCP server is restricted to GOLD SCHEMA ONLY for security.
- Database user has access to 27+ schemas
- MCP server enforces gold schema access only
- Prevents accidental access to other data layers

## 🚀 Improved Server Management

### Monitor & Auto-Restart
```bash
cd /Users/parivallal/workspace/claud
./monitor_mcp.sh start       # Start server
./monitor_mcp.sh status      # Check status  
./monitor_mcp.sh restart     # Restart server
./monitor_mcp.sh monitor     # Auto-restart on failure
./monitor_mcp.sh clean       # Force cleanup
./monitor_mcp.sh logs        # View live logs
```

### Test Connection Stability
```bash
cd /Users/parivallal/workspace/claud
source venv/bin/activate
python test_stability.py    # Test connection reliability
```

### Manual Server Control
```bash
cd /Users/parivallal/workspace/claud
source venv/bin/activate
python server.py            # Start manually
```

## 🔧 Troubleshooting

### If Connection Fails:
1. Run: `./monitor_mcp.sh clean` - Force cleanup
2. Run: `./monitor_mcp.sh start` - Start fresh
3. Run: `python test_stability.py` - Test stability

### Common Issues:
- **Port in use**: Auto-detection finds alternative port (5440-5449)
- **SSH tunnel fails**: 3 retry attempts with exponential backoff  
- **DB connection timeout**: 15s timeout with 3 retry attempts
- **Stale processes**: Use `clean` command to force cleanup

## Configuration Files
- Server: `/path/to/your/redshift-mcp-server/server.py`
- Environment: `/path/to/your/redshift-mcp-server/.env` 
- Claude Config: `~/Library/Application Support/Claude/claude_desktop_config.json`

## SSH Tunnel Details
- Bastion Host: your-bastion-host.example.com:22
- SSH User: your_ssh_user
- SSH Key: /path/to/your/ssh-key.pem
- Local Port: 5439 (auto-finds alternatives if in use)

## Database Connection
- Host: your-cluster.region.redshift.amazonaws.com
- Database: your_database_name
- User: your_readonly_user
- Connection: Via SSH tunnel to localhost:5439
- **Restricted to**: Gold schema only

## Usage Examples in Claude Desktop
- "Show me what schemas I have access to" → Returns: ["gold"]
- "Show me all tables" → Returns 33 gold schema tables
- "Describe the campaign table" → Gold schema campaign table
- "Query SELECT * FROM country LIMIT 10" → Gold schema country table
- **BLOCKED**: Any attempt to access other schemas (public, bronze, etc.)