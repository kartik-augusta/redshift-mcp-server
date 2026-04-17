#!/bin/bash

# MCP Server Monitor & Auto-Restart Script
# This script monitors the MCP server and restarts it if it fails

SERVER_DIR="$(cd "$(dirname "$0")" && pwd)"
LOG_FILE="$SERVER_DIR/mcp_monitor.log"
PID_FILE="$SERVER_DIR/mcp_server.pid"

# Function to log with timestamp
log() {
    echo "$(date '+%Y-%m-%d %H:%M:%S') - $1" | tee -a "$LOG_FILE"
}

# Function to check if server is running
is_server_running() {
    if [ -f "$PID_FILE" ]; then
        local pid=$(cat "$PID_FILE")
        if ps -p "$pid" > /dev/null 2>&1; then
            return 0  # Server is running
        else
            rm -f "$PID_FILE"  # Clean up stale PID file
        fi
    fi
    return 1  # Server is not running
}

# Function to start the server
start_server() {
    log "Starting MCP server..."
    cd "$SERVER_DIR"
    
    # Activate virtual environment and start server in background
    source venv/bin/activate
    nohup python server.py > mcp_server.log 2>&1 &
    local server_pid=$!
    
    # Save PID
    echo "$server_pid" > "$PID_FILE"
    log "MCP server started with PID: $server_pid"
    
    # Wait a moment and check if it's still running
    sleep 3
    if is_server_running; then
        log "✅ MCP server started successfully"
        return 0
    else
        log "❌ MCP server failed to start"
        return 1
    fi
}

# Function to stop the server
stop_server() {
    if [ -f "$PID_FILE" ]; then
        local pid=$(cat "$PID_FILE")
        log "Stopping MCP server (PID: $pid)..."
        kill "$pid" 2>/dev/null
        sleep 2
        
        # Force kill if still running
        if ps -p "$pid" > /dev/null 2>&1; then
            log "Force killing MCP server..."
            kill -9 "$pid" 2>/dev/null
        fi
        
        rm -f "$PID_FILE"
        log "✅ MCP server stopped"
    fi
}

# Function to restart the server
restart_server() {
    log "🔄 Restarting MCP server..."
    stop_server
    sleep 2
    start_server
}

# Function to test connectivity
test_connectivity() {
    cd "$SERVER_DIR"
    source venv/bin/activate
    timeout 30s python test_connection.py > /dev/null 2>&1
    return $?
}

# Main monitoring loop
monitor_server() {
    log "🔍 Starting MCP server monitoring (PID: $$)"
    
    while true; do
        if is_server_running; then
            # Server is running, test connectivity
            if test_connectivity; then
                log "✅ Server is running and responsive"
            else
                log "❌ Server is running but not responsive - restarting"
                restart_server
            fi
        else
            log "❌ Server is not running - starting"
            start_server
        fi
        
        # Wait 30 seconds before next check
        sleep 30
    done
}

# Handle command line arguments
case "$1" in
    start)
        start_server
        ;;
    stop)
        stop_server
        ;;
    restart)
        restart_server
        ;;
    status)
        if is_server_running; then
            echo "✅ MCP server is running"
            if test_connectivity; then
                echo "✅ Server is responsive"
            else
                echo "❌ Server is not responsive"
            fi
        else
            echo "❌ MCP server is not running"
        fi
        ;;
    monitor)
        monitor_server
        ;;
    logs)
        if [ -f "$LOG_FILE" ]; then
            tail -f "$LOG_FILE"
        else
            echo "No log file found"
        fi
        ;;
    clean)
        stop_server
        # Clean up any stale SSH tunnels
        pkill -f "ssh.*5439" 2>/dev/null || true
        # Clean up any stale ports
        lsof -ti :5439 | xargs kill -9 2>/dev/null || true
        log "🧹 Cleanup completed"
        ;;
    *)
        echo "Usage: $0 {start|stop|restart|status|monitor|logs|clean}"
        echo ""
        echo "Commands:"
        echo "  start   - Start the MCP server"
        echo "  stop    - Stop the MCP server"
        echo "  restart - Restart the MCP server"
        echo "  status  - Check server status"
        echo "  monitor - Start monitoring (auto-restart on failure)"
        echo "  logs    - View live logs"
        echo "  clean   - Force cleanup of processes and ports"
        exit 1
        ;;
esac