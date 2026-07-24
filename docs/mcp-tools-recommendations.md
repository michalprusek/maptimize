# maptalk MCP tool suite — best practices & upgrade

This document captures (1) the authoritative best practices for building MCP
tools/prompts, and (2) how the maptalk MCP server (`mcp-server/maptalk_mcp/`) was
upgraded against them. It is the reference for future changes to the tool suite.

Sources:
[Writing effective tools for agents](https://www.anthropic.com/engineering/writing-tools-for-agents) ·
[Building effective agents](https://www.anthropic.com/engineering/building-effective-agents) ·
[MCP tools spec (2025-06-18)](https://modelcontextprotocol.io/specification/2025-06-18/server/tools) ·
[MCP security best practices](https://modelcontextprotocol.io/docs/tutorials/security/security_best_practices) ·
[Tool annotations](https://blog.modelcontextprotocol.io/posts/2026-03-16-tool-annotations/)

## Principles (apply these to any new tool)

1. **Tool descriptions are the #1 lever.** 3–4 sentences: what it does, when to
   use / when not, what each param means, caveats, and what it does *not* return.
2. **Fewer, more capable tools beat many overlapping ones.** Design around the
   agent's workflow, not around REST endpoints. Consolidate; don't proliferate.
3. **Return high-signal results** — human-readable names/pages over opaque ids;
   for search, retrieve what the agent needs next (here: the page images).
4. **Constrain inputs** — enums over free strings, sensible defaults, mark only
   the truly-required params `required`.
5. **Annotate honestly** — `readOnlyHint` / `destructiveHint` / `idempotentHint`
   / `openWorldHint` shape client consent UX. They are hints, **not** a security
   gate; real authz stays server-side.
6. **Errors steer the next call** — actionable, model-recoverable text, not bare
   tracebacks. Paginate with sane defaults and *truncate loudly*.
7. **Server `instructions`** are the connector-level system prompt (the workflow,
   the Vision-RAG framing, scoping) — put shared context there once, not in every
   tool description.
8. **Security for remote MCP**: validate the bearer on every request; scope by the
   token's user (never a tool arg); fail closed; least privilege; SSRF-guard any
   server-side fetch.

## What changed (v2.0.0)

The suite went from 17 tools with overlaps and stale descriptions to a
consolidated, annotated, paginated set.

| Area | Before | After |
|------|--------|-------|
| Tool count | 17, three overlapping search/list tools | 14 core + `list_folders` / `create_folder` / `move_document` |
| Search | `search_documents` (text refs), `semantic_search`, `semantic_image_search` | one **`search_documents`** — retrieval built in (`return=images` default, `refs` for a text list, `include_fov`) |
| Listing | `list_documents` + `find_documents` (same endpoint) | one **`find_documents`** (metadata filters + `skip` pagination + `X-Total-Count`) |
| Server prompt | none | `instructions` describing the Vision-RAG workflow; `version=2.0.0` |
| Annotations | none | every tool (`readOnly` reads, `destructive` delete, `openWorld` web_search) |
| Enums | free strings | `status`, `file_type` (incl. `text`), `mode`, `return` |
| Descriptions | some 1-liners; `find_documents` falsely claimed OCR full-text | enriched; OCR claim removed (system is OCR-free) |
| Structured output | none | search/find return `structuredContent` (no `outputSchema` — see below) |
| Resources | none | browsable document catalog `maptalk://document/{id}` |
| Prompts | none | `summarize_document`, `compare_documents`, `literature_search` |
| Zoom | none | `read_page_region` — high-DPI crop of a page region (added earlier) |

### Design decisions & gotchas (why the code looks the way it does)

- **No `outputSchema`.** The SDK enforces a declared `outputSchema` on *every*
  call, so the unauthorized / unknown-tool / generic-error paths (which return a
  bare text block) would be masked as "output validation error". We deliver
  `structuredContent` via the `(content, structured)` tuple return instead —
  structured data with no enforcement hazard.
- **`file_type` enum must include `text`.** `index_text` stores `file_type="text"`
  (outside `DocumentType`), and MCP input validation is on, so omitting it would
  hard-reject a legitimate filter.
- **`move_document` needs a custom handler.** The generic pipeline strips a null
  arg, so moving a document to the library root (`folder_id` omitted) must send an
  explicit `{"folder_id": null}` in the PATCH body.
- **Pagination is metadata-only.** Semantic search is top-`k` with no offset, so
  only `find_documents` paginates (via `skip` + the `X-Total-Count` header). The
  list body stays a bare array — the frontend file-explorer depends on it.
- **Resource/prompt handlers replicate the fail-closed bearer scoping** of the
  tool path; a remote request with no bearer gets an empty catalog / an error,
  never another user's documents via the env service login.
- **Page images stay inline** (not `resource_link`): the whole point of Vision-RAG
  is Claude reading the page image in context, so tool results must embed it.

## Deliberately deferred (not in this upgrade)

- **A read-only OAuth scope** — a connector that can search but not delete/index.
  Worthwhile for sharing a library; needs OAuth scope plumbing.
- **An eval harness** — realistic multi-step tasks run in a loop, tracking
  tool-call count and token use, feeding transcripts back to iterate on the tools.
  This is how you'd *measure* whether the consolidation helped.

## Where things live

| File | Role |
|------|------|
| `mcp-server/maptalk_mcp/tools.yaml` | the tool set (edit this to add/reword tools) |
| `mcp-server/maptalk_mcp/registry.py` | yaml → `types.Tool` (enum + annotations plumbing) |
| `mcp-server/maptalk_mcp/handlers.py` | composite handlers (search, folders, page/region) |
| `mcp-server/maptalk_mcp/server.py` | `instructions`, version, Resources, Prompts, fail-closed bearer |
| `backend/routers/rag.py`, `services/rag_service.py` | REST endpoints + `count_documents_metadata` |
