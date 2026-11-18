"""Plant-troubleshooting MCP server entrypoint. Run via: python -m mcp_server.server"""
from mcp.server.fastmcp import FastMCP

from mcp_server import alarm_tools, audit_tools, docs_tools, historian_tools, kg_tools

mcp = FastMCP("plant-troubleshooting")

historian_tools.register(mcp)
alarm_tools.register(mcp)
kg_tools.register(mcp)
docs_tools.register(mcp)
audit_tools.register(mcp)

if __name__ == "__main__":
    mcp.run(transport="stdio")
