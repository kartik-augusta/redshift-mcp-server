# Redshift AI Assistant (MCP Server) – Executive Guide

## Overview
The **Redshift AI Assistant** securely connects your organization’s enterprise AI (like Claude) directly to your Redshift Data Warehouse. By using the Model Context Protocol (MCP), it acts as an intelligent bridge, allowing users to ask plain-English questions about the business, and empowering the AI to independently navigate, understand, and extract insights directly from your data.

This means less time waiting for manual SQL reports, and faster, data-driven decision-making directly within your AI chat interface.

---

## Key Capabilities

The AI assistant has been equipped with a suite of **10 specialized tools** that it uses autonomously to answer your questions. These tools are strictly governed by enterprise security rules, ensuring it can only access approved areas (schemas) and cannot alter or delete any data (Read-Only).

### 1. Data Discovery & Navigation
Before answering a question, the AI needs to understand what data is available. It uses these tools to map out the landscape:
- **List Allowed Schemas (`get_allowed_schemas`, `list_schemas`):** Identifies which specific data departments or zones it has security clearance to access (e.g., Gold Data, Reports).
- **List Tables (`list_tables`):** Discovers all available datasets within an approved area (e.g., Sales, Inventory, User Activity).
- **Describe Table (`describe_table`):** Reads the exact blueprint of a dataset, understanding what each column means and what type of data it holds.
- **Search Columns (`search_columns`):** Acts as a global search engine. If you ask for "Revenue by Region", the AI will use this tool to instantly find which tables contain "Revenue" and "Region" across the entire warehouse.

### 2. Data Analysis & Extraction
Once the AI knows where the data lives, it extracts the insights you need:
- **Query Data (`query_data`):** The AI writes and executes secure, read-only SQL queries to answer your complex business questions. It automatically caps the data volume to ensure performance.
- **Sample Data (`sample_data`):** Takes a quick peek at a few rows of a table to understand the real-world format of the data before doing deep analysis.
- **Table Row Count (`table_row_count`):** Quickly gauges the size and scale of a dataset (e.g., "How many total users do we have?").

### 3. Reporting & Optimization
- **Export to CSV (`export_to_csv`):** If you need the raw data for Excel or a presentation, the AI can securely package the query results into a downloadable CSV file.
- **Explain Query (`explain_query`):** An internal optimization tool the AI uses to ensure its queries run efficiently without overloading the database.

---

## Security & Governance

Security is built into the foundation of the assistant:
1. **Strict Read-Only Access:** The AI is physically incapable of modifying, deleting, or corrupting database records.
2. **Schema Allowlisting:** The AI is locked to specific, pre-approved data schemas (e.g., `gold_capsaai`). It cannot browse unauthorized areas.
3. **Data Capping:** All data extractions are strictly limited (e.g., max 500 rows for analysis, max 5,000 rows for CSV exports) to prevent performance degradation on your warehouse.

---

## Quick Setup Guide

To connect your Claude Desktop application to this server, you will need to add the server's endpoint to your configuration.

### Using the Command Line
Run the following command in your terminal, replacing `<MCP_NAME>` with your preferred name (e.g., `redshift`) and `<MCP_URL>` with the provided server URL:

```bash
claude mcp add --transport sse <MCP_NAME> <MCP_URL>/sse
```

### Manual Configuration
Alternatively, you can manually update your Claude configuration file (located at `~/Library/Application Support/Claude/claude_desktop_config.json` on Mac or `%APPDATA%\Claude\claude_desktop_config.json` on Windows).

Add the following JSON block:

```json
{
  "mcpServers": {
    "<MCP_NAME>": {
        "transport": {
            "type": "sse",
            "url": "<MCP_URL>/sse"
        }
    }
  }
}
```

Once connected, simply restart Claude, and you can begin asking questions about your Redshift data warehouse immediately!
