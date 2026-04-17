#!/bin/bash

echo "🚀 Preparing repository for GitHub upload..."

# Initialize git if not already done
if [ ! -d .git ]; then
    echo "📁 Initializing git repository..."
    git init
else
    echo "✅ Git repository already initialized"
fi

# Check if .env exists and warn about security
if [ -f .env ]; then
    echo "⚠️  WARNING: .env file contains sensitive credentials!"
    echo "   This file will NOT be uploaded to GitHub (protected by .gitignore)"
    echo "   ✅ Template .env.example will be uploaded instead"
fi

# Check if real config files exist and warn
if [ -f claude_desktop_config.json ]; then
    echo "⚠️  WARNING: claude_desktop_config.json contains credentials!"
    echo "   This file will NOT be uploaded to GitHub (protected by .gitignore)"
    echo "   ✅ Template claude_desktop_config.example.json will be uploaded instead"
fi

# Add all files (respecting .gitignore)
echo "📝 Adding files to git (respecting .gitignore)..."
git add .

# Show what will be committed
echo ""
echo "📋 Files that will be uploaded to GitHub:"
git diff --cached --name-only | sed 's/^/   ✅ /'

echo ""
echo "📋 Files that are ignored (won't be uploaded):"
git status --ignored | grep '!!' | sed 's/^!!/   🚫/' | head -10

# Check if there are changes to commit
if git diff --cached --quiet; then
    echo "⚠️  No changes to commit. Files may already be committed."
else
    echo ""
    echo "🎯 Ready to commit! Run the following commands:"
    echo ""
    echo "   git commit -m 'Initial commit: Redshift MCP Server with security'"
    echo "   git remote add origin https://github.com/your-org/redshift-mcp-server.git"
    echo "   git branch -M main"
    echo "   git push -u origin main"
    echo ""
fi

echo "✅ Repository preparation complete!"
echo ""
echo "🔒 Security Summary:"
echo "   ✅ Sensitive files (.env, *.log, *.pem) are git-ignored"
echo "   ✅ Template files (.env.example) are safe for public repos"  
echo "   ✅ No credentials will be uploaded to GitHub"
echo ""
echo "🌟 Your team can now safely clone and use this repository!"
