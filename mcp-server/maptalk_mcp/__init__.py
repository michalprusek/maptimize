"""maptalk MCP server.

A thin MCP (Model Context Protocol) server that exposes the maptalk / maptimize
backend to MCP clients (Claude Code, Claude Desktop / Cowork). It is a pure REST
client: the backend keeps ownership of the Qwen encoder, pgvector search and all
access control. The set of tools is declared in an editable ``tools.yaml`` so
tools and their descriptions can be changed over time without touching code.
"""

__version__ = "0.1.0"
