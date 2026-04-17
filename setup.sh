#!/bin/bash
# Setup script for Redshift MCP Server

echo "Setting up Redshift MCP Server..."

# Check if Python 3 is installed
if ! command -v python3 &> /dev/null; then
    echo "Python 3 is required but not installed. Please install Python 3.8 or higher."
    exit 1
fi

# Create virtual environment
echo "Creating virtual environment..."
python3 -m venv venv

# Activate virtual environment
echo "Activating virtual environment..."
source venv/bin/activate

# Upgrade pip
echo "Upgrading pip..."
pip install --upgrade pip

# Install requirements
echo "Installing Python dependencies..."
pip install -r requirements.txt

# Create .env file from example if it doesn't exist
if [ ! -f .env ]; then
    echo "Creating .env file from template..."
    cp .env.example .env
    echo "⚠️  IMPORTANT: Edit .env file with your actual credentials before running the server!"
else
    echo "✅ .env file already exists"
fi

echo ""
echo "🎉 Setup complete! Next steps:"
echo "1. Edit .env file with your Redshift credentials"
echo "   - Update RS_HOST, RS_DB, RS_USER, RS_PASS"
echo "   - Configure SSH tunnel settings if needed"
echo "2. Test connection: python test_connection.py"
echo "3. Start server: python server.py"
echo ""
echo "📖 See README.md for detailed configuration instructions"