# maptalk MCP server

Gives Claude (Claude Code **and** Claude Desktop / Cowork) access to your
maptalk / maptimize backend over the [Model Context Protocol](https://modelcontextprotocol.io):

- **`search_documents`** — semantic search over your indexed library documents
- **`semantic_search`** — combined search over documents + microscopy images
- **`list_documents`** — inventory of your indexed documents
- **`search_within_document`** — Ctrl+F inside one document
- **`read_document_pages`** — read a document's pages as images (Vision RAG)
- **`web_search`** — public web search, independent of the backend's Gemini quota

It is a thin REST client. The backend keeps the Qwen encoder, pgvector search
and all document access control; the MCP server just calls `/api/rag/...` **as a
maptalk user**, so you see exactly the documents you'd see in the UI.

The tool set lives in [`maptalk_mcp/tools.yaml`](maptalk_mcp/tools.yaml) — edit
that file to add tools or reword descriptions, no code change required. See
[Editing the tools](#editing-the-tools).

---

## 1. Install

```bash
cd /home/cvat/maptimize/mcp-server
python3 -m venv .venv
.venv/bin/pip install -e .
```

`.venv/bin/python -m maptalk_mcp` now runs the server. Note that path — the
clients below point at it as `MAPTALK_MCP_PYTHON`.

Sanity check (should print the tool list as JSON and exit):

```bash
MAPTALK_EMAIL=you@example.com MAPTALK_PASSWORD=... \
  .venv/bin/python -m maptalk_mcp --help
```

## 2. Configure

The server is configured entirely via environment variables (full list in
[`.env.example`](.env.example)):

| Variable | Meaning |
|---|---|
| `MAPTALK_BASE_URL` | Backend URL. `http://localhost:7001` on the UTIA box; `https://maptimize.utia.cas.cz` from a laptop. |
| `MAPTALK_EMAIL` / `MAPTALK_PASSWORD` | maptalk user to act as (ACL applies as this user). |
| `MAPTALK_TOKEN` | Alternative: a pre-issued JWT (24h, not auto-refreshed). |
| `MAPTALK_TOOLS_FILE` | Override path to an editable `tools.yaml`. |

---

## 3. Use it in **Claude Code**

A project-scoped [`.mcp.json`](../.mcp.json) is already committed at the repo
root. It reads credentials from your shell env (nothing secret is committed):

```bash
export MAPTALK_MCP_PYTHON=/home/cvat/maptimize/mcp-server/.venv/bin/python
export MAPTALK_EMAIL=you@example.com
export MAPTALK_PASSWORD=your-password
# optional: export MAPTALK_BASE_URL=https://maptimize.utia.cas.cz
claude   # start Claude Code from the repo root; approve the "maptalk" server when prompted
```

Then, in Claude Code:

```
/mcp                      # shows "maptalk" connected and its tools
> search my documents for fixation protocols
> list my documents, then read the first 3 pages of document 12
```

Prefer the CLI instead of the committed file? Add a user-scoped server:

```bash
claude mcp add maptalk \
  --env MAPTALK_BASE_URL=http://localhost:7001 \
  --env MAPTALK_EMAIL=you@example.com \
  --env MAPTALK_PASSWORD=your-password \
  -- /home/cvat/maptimize/mcp-server/.venv/bin/python -m maptalk_mcp
```

---

## 4. Use it in **Claude Desktop / Cowork**

Claude Desktop runs local stdio MCP servers too. Edit its config file:

- Linux: `~/.config/Claude/claude_desktop_config.json`
- macOS: `~/Library/Application Support/Claude/claude_desktop_config.json`
- Windows: `%APPDATA%\Claude\claude_desktop_config.json`

```jsonc
{
  "mcpServers": {
    "maptalk": {
      "command": "/home/cvat/maptimize/mcp-server/.venv/bin/python",
      "args": ["-m", "maptalk_mcp"],
      "env": {
        "MAPTALK_BASE_URL": "https://maptimize.utia.cas.cz",
        "MAPTALK_EMAIL": "you@example.com",
        "MAPTALK_PASSWORD": "your-password"
      }
    }
  }
}
```

Use absolute paths (Desktop doesn't inherit your shell). Fully quit and reopen
Claude Desktop; the `maptalk` tools appear under the connectors/tools menu and
Cowork tasks can call them. A copy of this snippet is in
[`examples/claude_desktop_config.json`](examples/claude_desktop_config.json).

> On a laptop the backend is reached over `https://maptimize.utia.cas.cz`, so
> the box must be network-reachable and the MCP server (this package + a Python)
> installed locally.

---

## Editing the tools

`maptalk_mcp/tools.yaml` is the single source of truth for what Claude sees.

- **Reword a description / rename a tool / change a default** → edit the entry.
- **Add a simple REST tool** → add an entry with `handler: http_json`, a
  `method`, a `path` (use `{name}` for path params), and `params`.
- **Composite behaviour** (multi-call, images, external calls) → a small named
  handler in `maptalk_mcp/handlers.py` (`document_pages`, `web_search` are the
  examples), referenced by `handler:`.

Changes take effect on the next tool **call** immediately (the file is re-read
when its mtime changes). The visible tool **list** refreshes when the client
reconnects (`/mcp` reconnect in Claude Code, or restart Desktop).

To keep an editable copy outside the package, set `MAPTALK_TOOLS_FILE` to its
path.

---

## Testing

```bash
.venv/bin/pip install -e ".[dev]"
.venv/bin/python -m pytest
```

The suite mocks the backend (no credentials or GPU needed) and includes a live
MCP stdio handshake that lists the tools.

---

## Notes & limitations

- **Auth**: the backend has no API-key mechanism, only user login. The server
  logs in with `MAPTALK_EMAIL`/`MAPTALK_PASSWORD` and re-authenticates once on a
  401. A dedicated service user is the tidy pattern for shared/team use.
- **Transport**: stdio only for now (perfect for Claude Code + Desktop with a
  local server). A hosted Streamable-HTTP endpoint — so laptops need no local
  install — is the natural next step: serve `maptalk_mcp` behind the existing
  nginx at `maptimize.utia.cas.cz/mcp` and add it via Claude's custom connectors
  with a bearer header. Not shipped here because it needs TLS + a transport
  allowlist and couldn't be end-to-end tested in this pass.
- **web_search** uses DuckDuckGo's HTML endpoint (no API key). It's best-effort;
  Claude Code/Desktop also have native web search.
