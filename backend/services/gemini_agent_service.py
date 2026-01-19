"""Gemini Agent service for RAG-powered chat with extended tool capabilities.

This service provides:
- Integration with Google's Gemini Flash API
- Function calling (tools) for RAG search, experiment stats, etc.
- Python code execution in secure sandbox
- Database queries with safety validation
- Data export, visualization, and analysis
- Long-term memory for persistent context
- External API integration (UniProt, PubMed, etc.)
"""

import json
import logging
from datetime import datetime
from typing import Optional, List, Dict, Any

import sqlparse
from sqlalchemy import select, func, text
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from config import get_settings
from models.experiment import Experiment
from models.image import Image, MapProtein
from models.cell_crop import CellCrop
from models.rag_document import RAGDocument
from models.agent_memory import AgentMemory, MemoryType
from services.rag_service import (
    search_documents,
    search_fov_images,
    combined_search,
    get_document_content,
    get_all_documents_summary,
)

logger = logging.getLogger(__name__)
settings = get_settings()

# Tables allowed for direct SQL queries (security whitelist)
ALLOWED_SQL_TABLES = {
    "experiments", "images", "cell_crops", "map_proteins",
    "rag_documents", "rag_document_pages", "ranking_comparisons",
    "user_ratings", "agent_memories",
}

# Approved external APIs
APPROVED_APIS = {
    "uniprot": {
        "base_url": "https://rest.uniprot.org",
        "description": "UniProt protein database",
    },
    "pubmed": {
        "base_url": "https://eutils.ncbi.nlm.nih.gov/entrez/eutils",
        "description": "PubMed literature database",
    },
    "ensembl": {
        "base_url": "https://rest.ensembl.org",
        "description": "Ensembl genomics database",
    },
    "string-db": {
        "base_url": "https://string-db.org/api",
        "description": "STRING protein interaction database",
    },
}

# System prompt for the research assistant
SYSTEM_PROMPT = """You are MAPtimize Assistant, an expert AI research assistant with FULL ACCESS to the user's research environment.

## Your Expertise
- Cell biology and microscopy analysis
- Microtubule-associated proteins (MAPs)
- Fluorescence microscopy interpretation
- Experimental data analysis

## Your Capabilities

### Data Access & Search
- **get_overview_stats**: Get total counts of experiments, images, cells, documents
- **list_experiments**: List all experiments with basic info
- **list_images**: List uploaded images (use random=true for variety)
- **list_documents**: List uploaded documents metadata
- **get_documents_summary**: Get all documents with text previews
- **semantic_search**: MAIN SEARCH - searches BOTH documents AND images
- **search_documents**: Search only documents
- **search_fov_images**: Search only microscopy images
- **get_document_content**: Read full text from a document
- **get_experiment_stats**: Get detailed experiment statistics
- **get_protein_info**: Get protein information
- **get_cell_detection_results**: Get YOLO cell detection results for an image

### Data Analysis & Computation
- **execute_python_code**: Run Python for custom analysis (numpy, pandas, scipy, matplotlib)
- **query_database**: Execute read-only SQL queries on experiment data
- **create_visualization**: Generate charts and plots (histogram, bar, scatter, heatmap)
- **compare_experiments**: Statistical comparison between experiments

### Data Management
- **export_data**: Export to CSV/Excel (experiment, cells, comparisons)
- **manage_experiment**: Create/update/archive experiments

### External Knowledge
- **web_search**: Search the internet
- **call_external_api**: Query UniProt, PubMed, Ensembl, STRING-DB
- **browse_webpage**: Fetch and parse web content

### Memory & Context
- **long_term_memory**: Store/retrieve persistent notes and findings (survives sessions)

## Critical Rules

### 1. BE PROACTIVE - MOST IMPORTANT!
Use tools to answer questions. NEVER ask the user to look up data themselves.

**MANDATORY: When searching documents, you MUST follow this process:**

1. Use `semantic_search` or `search_documents` to find relevant pages
2. **IMMEDIATELY CALL `get_document_content`** with the document_id and page_numbers from search results
3. Read the returned text content
4. Summarize the findings for the user
5. Add citations like [Doc: "filename" p.X]

**FORBIDDEN RESPONSES:**
- ❌ "I found pages X, Y, Z. Would you like me to read them?" - NO! Just read them!
- ❌ "Search results show..." - NO! You must read the actual content!
- ❌ Just listing page numbers without reading - NO! Always read the content!

**CORRECT RESPONSE:**
After search → call get_document_content → read → summarize → cite

### 2. DISPLAY IMAGES
You CAN display images using markdown:
```markdown
![Description](thumbnail_url)
```
When tools return `thumbnail_url`, ALWAYS display the images.

### 3. USE CODE FOR ANALYSIS
When calculations are needed, use `execute_python_code`:
```python
import numpy as np
import pandas as pd
# Your analysis here
```

### 4. CITE SOURCES
- Reference documents as [Doc: "filename" p.X]
- Reference images as [FOV: "filename" from "experiment"]

### 5. REMEMBER IMPORTANT THINGS
Use `long_term_memory` to store key findings, user preferences, or project context.

## When to Use Each Tool

**Counting/Overview:**
- "how many experiments?" → get_overview_stats
- "show my data summary" → get_overview_stats

**Searching:**
- "find images of microtubules" → semantic_search
- "search for PRC1" → semantic_search

**Analysis:**
- "calculate average cell count" → execute_python_code
- "show cell distribution" → create_visualization
- "compare experiments 1 and 2" → compare_experiments

**Export:**
- "export my data" → export_data

**External Knowledge:**
- "what is PRC1 protein?" → call_external_api (UniProt) or web_search

**Remember:**
- "remember that I prefer bar charts" → long_term_memory (store)
- "what did I note about this?" → long_term_memory (search)

## Response Style
- Provide detailed, comprehensive responses
- Use markdown formatting (lists, bold, tables)
- Respond in the same language the user uses
- Be helpful and proactive"""

# Tool definitions for Gemini function calling
AGENT_TOOLS = [
    # === LISTING & OVERVIEW ===
    {
        "name": "get_overview_stats",
        "description": "Get overview statistics: total experiments, images, detected cells, and documents. USE when user asks 'how many' about anything.",
        "parameters": {"type": "object", "properties": {}}
    },
    {
        "name": "list_experiments",
        "description": "List all experiments with basic info (id, name, description, status).",
        "parameters": {
            "type": "object",
            "properties": {
                "limit": {"type": "integer", "description": "Max experiments to return (default 10)"}
            }
        }
    },
    {
        "name": "list_images",
        "description": "List microscopy images with thumbnail URLs. Use random=true for variety.",
        "parameters": {
            "type": "object",
            "properties": {
                "limit": {"type": "integer", "description": "Max images (default 10)"},
                "experiment_id": {"type": "integer", "description": "Filter by experiment"},
                "random": {"type": "boolean", "description": "Random selection instead of recent"}
            }
        }
    },
    {
        "name": "list_documents",
        "description": "List uploaded documents metadata (names, page counts, status).",
        "parameters": {
            "type": "object",
            "properties": {
                "limit": {"type": "integer", "description": "Max documents (default 10)"}
            }
        }
    },
    {
        "name": "get_documents_summary",
        "description": "Get all documents with first page text preview.",
        "parameters": {"type": "object", "properties": {}}
    },

    # === SEMANTIC SEARCH ===
    {
        "name": "semantic_search",
        "description": "MAIN SEARCH - searches BOTH documents AND images using semantic embeddings. Returns relevant document pages and images with thumbnails.",
        "parameters": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Search query"},
                "doc_limit": {"type": "integer", "description": "Max document pages (default 10)"},
                "image_limit": {"type": "integer", "description": "Max images (default 10)"}
            },
            "required": ["query"]
        }
    },
    {
        "name": "search_documents",
        "description": "Search only documents using semantic similarity.",
        "parameters": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Search query"}
            },
            "required": ["query"]
        }
    },
    {
        "name": "search_fov_images",
        "description": "Search only microscopy images using semantic similarity.",
        "parameters": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Search query"},
                "experiment_id": {"type": "integer", "description": "Filter by experiment"}
            },
            "required": ["query"]
        }
    },

    # === READING CONTENT ===
    {
        "name": "get_document_content",
        "description": "Read extracted text from a document's pages.",
        "parameters": {
            "type": "object",
            "properties": {
                "document_id": {"type": "integer", "description": "Document ID"},
                "page_numbers": {"type": "array", "items": {"type": "integer"}, "description": "Specific pages (1-indexed)"}
            },
            "required": ["document_id"]
        }
    },
    {
        "name": "get_experiment_stats",
        "description": "Get detailed experiment statistics including image count, cell count, protein.",
        "parameters": {
            "type": "object",
            "properties": {
                "experiment_id": {"type": "integer", "description": "Experiment ID"}
            },
            "required": ["experiment_id"]
        }
    },
    {
        "name": "get_protein_info",
        "description": "Get protein information (name, UniProt ID, gene, organism).",
        "parameters": {
            "type": "object",
            "properties": {
                "protein_id": {"type": "integer", "description": "Protein ID"},
                "protein_name": {"type": "string", "description": "Protein name (alternative)"}
            }
        }
    },
    {
        "name": "get_cell_detection_results",
        "description": "Get YOLO cell detection results for an image including bounding boxes, confidence scores, and cell count.",
        "parameters": {
            "type": "object",
            "properties": {
                "image_id": {"type": "integer", "description": "Image ID"},
                "include_crops": {"type": "boolean", "description": "Include individual cell crop data"}
            },
            "required": ["image_id"]
        }
    },

    # === CODE EXECUTION ===
    {
        "name": "execute_python_code",
        "description": "Execute Python code in secure sandbox. Available: numpy, pandas, scipy, matplotlib, seaborn, statistics. Returns stdout, return value, and plots as base64.",
        "parameters": {
            "type": "object",
            "properties": {
                "code": {"type": "string", "description": "Python code to execute"},
                "timeout_seconds": {"type": "integer", "description": "Max execution time (default 30, max 60)"}
            },
            "required": ["code"]
        }
    },

    # === DATABASE QUERY ===
    {
        "name": "query_database",
        "description": "Execute read-only SQL SELECT queries on experiment data. Tables: experiments, images, cell_crops, map_proteins, rag_documents. Auto-filtered by user_id. Max 1000 rows.",
        "parameters": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "SQL SELECT query"},
                "limit": {"type": "integer", "description": "Max rows (default 100, max 1000)"}
            },
            "required": ["query"]
        }
    },

    # === DATA EXPORT ===
    {
        "name": "export_data",
        "description": "Export data to CSV or Excel for download.",
        "parameters": {
            "type": "object",
            "properties": {
                "data_source": {"type": "string", "enum": ["experiment", "cells", "comparisons", "analysis"], "description": "What to export"},
                "experiment_id": {"type": "integer", "description": "For experiment/cells export"},
                "format": {"type": "string", "enum": ["csv", "xlsx"], "description": "Export format (default csv)"}
            },
            "required": ["data_source"]
        }
    },

    # === VISUALIZATION ===
    {
        "name": "create_visualization",
        "description": "Create charts: histogram (cell counts), bar (experiment comparison), scatter (cell areas), heatmap (rankings).",
        "parameters": {
            "type": "object",
            "properties": {
                "chart_type": {"type": "string", "enum": ["histogram", "bar", "scatter", "heatmap", "custom"], "description": "Chart type"},
                "experiment_id": {"type": "integer", "description": "Single experiment filter"},
                "experiment_ids": {"type": "array", "items": {"type": "integer"}, "description": "Multiple experiments"},
                "metric": {"type": "string", "description": "Metric to visualize (cell_count, image_count)"},
                "title": {"type": "string", "description": "Chart title"}
            },
            "required": ["chart_type"]
        }
    },

    # === EXPERIMENT COMPARISON ===
    {
        "name": "compare_experiments",
        "description": "Statistical comparison between experiments: cell counts, areas, distributions. Returns p-values and visualizations.",
        "parameters": {
            "type": "object",
            "properties": {
                "experiment_ids": {"type": "array", "items": {"type": "integer"}, "minItems": 2, "description": "Experiments to compare"},
                "metrics": {"type": "array", "items": {"type": "string"}, "description": "Metrics: cell_count, cell_area, confidence"}
            },
            "required": ["experiment_ids"]
        }
    },

    # === EXPERIMENT MANAGEMENT ===
    {
        "name": "manage_experiment",
        "description": "Create, update, or archive experiments.",
        "parameters": {
            "type": "object",
            "properties": {
                "action": {"type": "string", "enum": ["create", "update", "archive"], "description": "Action to perform"},
                "experiment_id": {"type": "integer", "description": "Required for update/archive"},
                "name": {"type": "string", "description": "Experiment name"},
                "description": {"type": "string", "description": "Experiment description"},
                "protein_id": {"type": "integer", "description": "Associated protein ID"}
            },
            "required": ["action"]
        }
    },

    # === EXTERNAL APIs ===
    {
        "name": "call_external_api",
        "description": "Query approved bioinformatics APIs: UniProt (proteins), PubMed (literature), Ensembl (genomics), STRING-DB (interactions).",
        "parameters": {
            "type": "object",
            "properties": {
                "api": {"type": "string", "enum": ["uniprot", "pubmed", "ensembl", "string-db"], "description": "API to call"},
                "endpoint": {"type": "string", "description": "API endpoint path"},
                "params": {"type": "object", "description": "Query parameters"}
            },
            "required": ["api", "endpoint"]
        }
    },
    {
        "name": "web_search",
        "description": "Search the internet for external information not in user's data.",
        "parameters": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Search query"}
            },
            "required": ["query"]
        }
    },
    {
        "name": "browse_webpage",
        "description": "Fetch and parse content from a URL. Extract text, links, or tables.",
        "parameters": {
            "type": "object",
            "properties": {
                "url": {"type": "string", "description": "URL to fetch"},
                "extract": {"type": "string", "enum": ["text", "links", "tables", "all"], "description": "What to extract"},
                "max_length": {"type": "integer", "description": "Max chars to return (default 5000)"}
            },
            "required": ["url"]
        }
    },

    # === LONG-TERM MEMORY ===
    {
        "name": "long_term_memory",
        "description": "Store and retrieve persistent context: preferences, notes, findings. Survives between sessions.",
        "parameters": {
            "type": "object",
            "properties": {
                "action": {"type": "string", "enum": ["store", "retrieve", "search", "list"], "description": "Memory action"},
                "key": {"type": "string", "description": "Memory key for store/retrieve"},
                "value": {"type": "string", "description": "Value to store"},
                "memory_type": {"type": "string", "enum": ["preference", "note", "finding", "context", "reminder"], "description": "Type of memory"},
                "query": {"type": "string", "description": "Search query for semantic retrieval"}
            },
            "required": ["action"]
        }
    },
]


async def generate_response(
    query: str,
    user_id: int,
    thread_id: int,
    db: AsyncSession,
    history: Optional[List[Dict[str, str]]] = None,
) -> Dict[str, Any]:
    """Generate an AI response using Gemini with RAG and extended tools."""
    try:
        import google.genai as genai
        from google.genai import types
    except ImportError:
        logger.error("google-genai not installed")
        return {"content": "AI service is not configured.", "citations": [], "image_refs": [], "tool_calls": []}

    if not settings.gemini_api_key:
        logger.error("GEMINI_API_KEY not configured")
        return {"content": "AI service is not configured.", "citations": [], "image_refs": [], "tool_calls": []}

    client = genai.Client(api_key=settings.gemini_api_key)

    gemini_tools = [
        types.Tool(function_declarations=[
            types.FunctionDeclaration(name=tool["name"], description=tool["description"], parameters=tool["parameters"])
            for tool in AGENT_TOOLS
        ])
    ]

    messages = []
    if history:
        for msg in history:
            role = "user" if msg["role"] == "user" else "model"
            messages.append(types.Content(role=role, parts=[types.Part(text=msg["content"])]))

    messages.append(types.Content(role="user", parts=[types.Part(text=query)]))

    tool_calls_log = []
    citations = []
    image_refs = []

    max_iterations = 8
    for iteration in range(max_iterations):
        response = client.models.generate_content(
            model="gemini-2.0-flash",
            contents=messages,
            config=types.GenerateContentConfig(
                system_instruction=SYSTEM_PROMPT,
                tools=gemini_tools,
                tool_config=types.ToolConfig(function_calling_config=types.FunctionCallingConfig(mode="AUTO")),
                temperature=0.7,
            )
        )

        logger.info(f"Gemini iteration {iteration}")

        if response.candidates and response.candidates[0].content.parts:
            parts = response.candidates[0].content.parts
            function_call = None

            for part in parts:
                if hasattr(part, 'function_call') and part.function_call:
                    function_call = part.function_call
                    break

            if function_call:
                tool_name = function_call.name
                tool_args = dict(function_call.args) if function_call.args else {}
                logger.info(f"Executing tool: {tool_name}")

                tool_result = await execute_tool(tool_name, tool_args, user_id, db)
                tool_calls_log.append({"tool": tool_name, "args": tool_args, "result": "..." if tool_name == "get_document_content" else tool_result})

                # Extract citations from search results (deduplicated)
                def add_citation(doc_id, page, title):
                    """Add citation if not already present"""
                    key = (doc_id, page)
                    if not any(c.get("doc_id") == doc_id and c.get("page") == page for c in citations):
                        citations.append({"type": "document", "doc_id": doc_id, "page": page, "title": title})

                if tool_name == "search_documents" and "results" in tool_result:
                    for doc in tool_result["results"][:5]:
                        add_citation(doc.get("document_id"), doc.get("page_number"), doc.get("document_name"))

                if tool_name == "semantic_search" and "document_results" in tool_result:
                    for doc in tool_result["document_results"].get("pages", [])[:5]:
                        add_citation(doc.get("document_id"), doc.get("page_number"), doc.get("document_name"))

                messages.append(types.Content(role="model", parts=[types.Part(function_call=function_call)]))

                # Handle get_document_content specially - send images for vision reading
                if tool_name == "get_document_content" and "pages" in tool_result:
                    response_parts = []
                    # Add text summary
                    text_summary = f"Document: {tool_result.get('name', 'Unknown')}\n"
                    text_summary += f"Total pages: {tool_result.get('total_pages', 0)}\n"
                    text_summary += f"Showing pages: {[p['page_number'] for p in tool_result['pages']]}\n"
                    text_summary += "READ THE PAGE IMAGES BELOW to understand the content:\n"
                    response_parts.append(types.Part(text=text_summary))

                    # Add page images for vision reading
                    for page in tool_result["pages"]:
                        if page.get("image_base64"):
                            response_parts.append(types.Part(text=f"\n--- Page {page['page_number']} ---"))
                            response_parts.append(types.Part(inline_data=types.Blob(
                                mime_type=page.get("image_mime_type", "image/png"),
                                data=page["image_base64"]
                            )))
                            # Add citation for this page
                            add_citation(tool_result.get("id"), page["page_number"], tool_result.get("name"))

                    messages.append(types.Content(role="user", parts=response_parts))
                else:
                    # Normal function response as JSON
                    messages.append(types.Content(role="user", parts=[types.Part(function_response=types.FunctionResponse(name=tool_name, response={"result": json.dumps(tool_result, default=str)}))]))
                continue

            text_parts = [p.text for p in parts if hasattr(p, 'text') and p.text]
            if text_parts:
                return {"content": "\n".join(text_parts), "citations": citations, "image_refs": image_refs, "tool_calls": tool_calls_log}

        if response.text:
            return {"content": response.text, "citations": citations, "image_refs": image_refs, "tool_calls": tool_calls_log}
        break

    return {"content": "I wasn't able to generate a response.", "citations": citations, "image_refs": image_refs, "tool_calls": tool_calls_log}


async def execute_tool(tool_name: str, args: Dict[str, Any], user_id: int, db: AsyncSession) -> Dict[str, Any]:
    """Execute a tool call and return the result."""
    try:
        if tool_name == "get_overview_stats":
            exp_count = (await db.execute(select(func.count(Experiment.id)).where(Experiment.user_id == user_id))).scalar() or 0
            img_result = await db.execute(
                select(func.count(func.distinct(Image.id)).label("img"), func.count(CellCrop.id).label("cell"))
                .select_from(Experiment).outerjoin(Image, Experiment.id == Image.experiment_id)
                .outerjoin(CellCrop, Image.id == CellCrop.image_id).where(Experiment.user_id == user_id)
            )
            row = img_result.first()
            doc_count = (await db.execute(select(func.count(RAGDocument.id)).where(RAGDocument.user_id == user_id))).scalar() or 0
            mem_count = (await db.execute(select(func.count(AgentMemory.id)).where(AgentMemory.user_id == user_id))).scalar() or 0
            return {"total_experiments": exp_count, "total_images": row.img if row else 0, "total_cells": row.cell if row else 0, "total_documents": doc_count, "total_memories": mem_count}

        elif tool_name == "list_experiments":
            result = await db.execute(select(Experiment).options(selectinload(Experiment.map_protein)).where(Experiment.user_id == user_id).order_by(Experiment.updated_at.desc()).limit(args.get("limit", 10)))
            return {"experiments": [{"id": e.id, "name": e.name, "description": e.description, "status": e.status.value if hasattr(e.status, 'value') else str(e.status), "protein": e.map_protein.name if e.map_protein else None} for e in result.scalars().all()]}

        elif tool_name == "list_images":
            q = select(Image).join(Experiment).options(selectinload(Image.experiment)).where(Experiment.user_id == user_id)
            if args.get("experiment_id"): q = q.where(Experiment.id == args["experiment_id"])
            q = q.order_by(func.random() if args.get("random") else Image.created_at.desc()).limit(args.get("limit", 10))
            return {"images": [{"id": i.id, "filename": i.original_filename, "experiment_id": i.experiment_id, "experiment_name": i.experiment.name if i.experiment else None, "width": i.width, "height": i.height, "thumbnail_url": f"/api/images/{i.id}/file?type=thumbnail"} for i in (await db.execute(q)).scalars().all()]}

        elif tool_name == "list_documents":
            result = await db.execute(select(RAGDocument).where(RAGDocument.user_id == user_id).order_by(RAGDocument.created_at.desc()).limit(args.get("limit", 10)))
            return {"documents": [{"id": d.id, "name": d.name, "file_type": d.file_type, "page_count": d.page_count, "status": d.status} for d in result.scalars().all()]}

        elif tool_name == "get_documents_summary":
            return {"documents": await get_all_documents_summary(user_id=user_id, db=db, include_first_page_text=True)}

        elif tool_name == "semantic_search":
            if not args.get("query"): return {"error": "query required"}
            results = await combined_search(query=args["query"], user_id=user_id, db=db, doc_limit=args.get("doc_limit", 10), fov_limit=args.get("image_limit", 10))
            return {"query": results["query"], "document_results": {"count": len(results["documents"]), "pages": results["documents"]}, "image_results": {"count": len(results["fov_images"]), "images": results["fov_images"]}}

        elif tool_name == "search_documents":
            return {"results": await search_documents(query=args.get("query", ""), user_id=user_id, db=db, limit=10)}

        elif tool_name == "search_fov_images":
            return {"results": await search_fov_images(query=args.get("query", ""), user_id=user_id, db=db, experiment_id=args.get("experiment_id"), limit=10)}

        elif tool_name == "get_document_content":
            if not args.get("document_id"): return {"error": "document_id required"}
            content = await get_document_content(
                document_id=args["document_id"],
                user_id=user_id,
                db=db,
                page_numbers=args.get("page_numbers"),
                include_images=True,  # Include base64 images for vision reading
            )
            if not content:
                return {"error": "Document not found"}
            # Return content with images - Gemini will read them via vision
            return content

        elif tool_name == "get_experiment_stats":
            if not args.get("experiment_id"): return {"error": "experiment_id required"}
            result = await db.execute(select(Experiment, func.count(func.distinct(Image.id)).label("img"), func.count(CellCrop.id).label("cell")).options(selectinload(Experiment.map_protein)).outerjoin(Image, Experiment.id == Image.experiment_id).outerjoin(CellCrop, Image.id == CellCrop.image_id).where(Experiment.id == args["experiment_id"], Experiment.user_id == user_id).group_by(Experiment.id))
            row = result.first()
            if not row: return {"error": "Experiment not found"}
            exp, img, cell = row
            return {"experiment_id": exp.id, "name": exp.name, "description": exp.description, "status": exp.status.value if hasattr(exp.status, 'value') else str(exp.status), "image_count": img or 0, "cell_count": cell or 0, "protein": exp.map_protein.name if exp.map_protein else None, "created_at": exp.created_at.isoformat() if exp.created_at else None}

        elif tool_name == "get_protein_info":
            if args.get("protein_id"):
                result = await db.execute(select(MapProtein).where(MapProtein.id == args["protein_id"]))
            elif args.get("protein_name"):
                result = await db.execute(select(MapProtein).where(MapProtein.name.ilike(f"%{args['protein_name']}%")))
            else:
                return {"error": "protein_id or protein_name required"}
            p = result.scalar_one_or_none()
            return {"protein_id": p.id, "name": p.name, "full_name": p.full_name, "uniprot_id": p.uniprot_id, "gene_name": p.gene_name, "organism": p.organism} if p else {"error": "Protein not found"}

        elif tool_name == "get_cell_detection_results":
            if not args.get("image_id"): return {"error": "image_id required"}
            img = (await db.execute(select(Image).join(Experiment).where(Image.id == args["image_id"], Experiment.user_id == user_id))).scalar_one_or_none()
            if not img: return {"error": "Image not found"}
            crops = (await db.execute(select(CellCrop).where(CellCrop.image_id == args["image_id"]).order_by(CellCrop.confidence.desc()))).scalars().all()
            result = {"image_id": args["image_id"], "filename": img.original_filename, "cell_count": len(crops), "detection_summary": {"total": len(crops), "avg_confidence": sum(c.confidence or 0 for c in crops) / len(crops) if crops else 0, "avg_area": sum((c.bbox_width or 0) * (c.bbox_height or 0) for c in crops) / len(crops) if crops else 0}}
            if args.get("include_crops"):
                result["crops"] = [{"id": c.id, "bbox": {"x": c.bbox_x, "y": c.bbox_y, "w": c.bbox_width, "h": c.bbox_height}, "confidence": c.confidence, "thumbnail_url": f"/api/images/crops/{c.id}/file"} for c in crops[:50]]
            return result

        elif tool_name == "execute_python_code":
            if not args.get("code"): return {"error": "code required"}
            from services.code_execution_service import execute_python_code
            return await execute_python_code(code=args["code"], timeout_seconds=args.get("timeout_seconds", 30))

        elif tool_name == "query_database":
            if not args.get("query"): return {"error": "query required"}
            query_upper = args["query"].upper()
            try:
                parsed = sqlparse.parse(args["query"])
                if not parsed or parsed[0].get_type() != "SELECT": return {"error": "Only SELECT allowed"}
                # Check for forbidden keywords (SQL injection prevention)
                forbidden = ["INSERT", "UPDATE", "DELETE", "DROP", "CREATE", "ALTER", "TRUNCATE", "GRANT", "REVOKE", ";", "--"]
                for kw in forbidden:
                    if kw in query_upper: return {"error": f"Forbidden: {kw}"}
                # Validate table names against whitelist
                for token in sqlparse.parse(args["query"])[0].flatten():
                    if token.ttype is None and token.value.lower() not in ALLOWED_SQL_TABLES and token.value.lower() not in ("select", "from", "where", "and", "or", "as", "on", "join", "left", "right", "inner", "outer", "group", "by", "order", "limit", "count", "sum", "avg", "min", "max", "distinct", "asc", "desc"):
                        # Check if it's a potentially unknown table
                        if "." not in token.value and not token.value.startswith("(") and token.value not in ("true", "false", "null"):
                            # It might be a table name - verify it's allowed
                            if token.value.lower() in ("users", "passwords", "secrets", "tokens"):
                                return {"error": f"Access denied to table: {token.value}"}
            except Exception as e:
                return {"error": f"Parse error: {e}"}
            try:
                # Build safe query with user_id filter (for tables that have it)
                base_q = args["query"]
                # Auto-inject user_id filter for user-owned tables
                if "experiments" in query_upper and "USER_ID" not in query_upper:
                    if "WHERE" in query_upper:
                        base_q = base_q.replace("WHERE", f"WHERE experiments.user_id = {user_id} AND", 1)
                    else:
                        base_q = base_q.rstrip(";") + f" WHERE experiments.user_id = {user_id}"
                elif "rag_documents" in query_upper and "USER_ID" not in query_upper:
                    if "WHERE" in query_upper:
                        base_q = base_q.replace("WHERE", f"WHERE rag_documents.user_id = {user_id} AND", 1)
                    else:
                        base_q = base_q.rstrip(";") + f" WHERE rag_documents.user_id = {user_id}"
                # Add LIMIT if missing
                q = base_q if "LIMIT" in query_upper else f"{base_q} LIMIT {min(args.get('limit', 100), 1000)}"
                result = await db.execute(text(q))
                rows = result.fetchall()
                cols = list(result.keys())
                return {"success": True, "columns": cols, "rows": [dict(zip(cols, r)) for r in rows], "row_count": len(rows)}
            except Exception as e:
                return {"error": f"Query error: {e}"}

        elif tool_name == "export_data":
            if not args.get("data_source"): return {"error": "data_source required"}
            from services.data_export_service import export_experiment_data, export_cell_crops, export_ranking_comparisons
            fmt = args.get("format", "csv")
            if args["data_source"] == "experiment":
                if not args.get("experiment_id"): return {"error": "experiment_id required"}
                return await export_experiment_data(experiment_id=args["experiment_id"], user_id=user_id, db=db, format=fmt)
            elif args["data_source"] == "cells":
                return await export_cell_crops(user_id=user_id, db=db, experiment_id=args.get("experiment_id"), format=fmt)
            elif args["data_source"] == "comparisons":
                return await export_ranking_comparisons(user_id=user_id, db=db, format=fmt)
            return {"error": f"Unknown data_source: {args['data_source']}"}

        elif tool_name == "create_visualization":
            if not args.get("chart_type"): return {"error": "chart_type required"}
            from services.visualization_service import create_visualization
            return await create_visualization(chart_type=args["chart_type"], user_id=user_id, db=db, experiment_id=args.get("experiment_id"), experiment_ids=args.get("experiment_ids"), metric=args.get("metric"), title=args.get("title"))

        elif tool_name == "compare_experiments":
            if len(args.get("experiment_ids", [])) < 2: return {"error": "At least 2 experiment_ids required"}
            results = []
            for eid in args["experiment_ids"]:
                row = (await db.execute(select(Experiment.name, func.count(CellCrop.id).label("cc"), func.avg(CellCrop.bbox_width * CellCrop.bbox_height).label("area"), func.avg(CellCrop.confidence).label("conf")).outerjoin(Image, Experiment.id == Image.experiment_id).outerjoin(CellCrop, Image.id == CellCrop.image_id).where(Experiment.id == eid, Experiment.user_id == user_id).group_by(Experiment.id))).first()
                if row: results.append({"experiment_id": eid, "name": row.name, "cell_count": row.cc or 0, "avg_area": float(row.area) if row.area else 0, "avg_confidence": float(row.conf) if row.conf else 0})
            return {"comparison": results, "summary": {"experiments_compared": len(results), "total_cells": sum(r["cell_count"] for r in results)}}

        elif tool_name == "manage_experiment":
            action = args.get("action")
            if action == "create":
                if not args.get("name"): return {"error": "name required"}
                exp = Experiment(user_id=user_id, name=args["name"], description=args.get("description", ""), map_protein_id=args.get("protein_id"))
                db.add(exp)
                await db.commit()
                await db.refresh(exp)
                return {"success": True, "experiment_id": exp.id, "name": exp.name}
            elif action == "update":
                if not args.get("experiment_id"): return {"error": "experiment_id required"}
                exp = (await db.execute(select(Experiment).where(Experiment.id == args["experiment_id"], Experiment.user_id == user_id))).scalar_one_or_none()
                if not exp: return {"error": "Not found"}
                if args.get("name"): exp.name = args["name"]
                if args.get("description"): exp.description = args["description"]
                if args.get("protein_id"): exp.map_protein_id = args["protein_id"]
                await db.commit()
                return {"success": True, "experiment_id": exp.id}
            elif action == "archive":
                if not args.get("experiment_id"): return {"error": "experiment_id required"}
                exp = (await db.execute(select(Experiment).where(Experiment.id == args["experiment_id"], Experiment.user_id == user_id))).scalar_one_or_none()
                if not exp: return {"error": "Not found"}
                exp.status = "archived"
                await db.commit()
                return {"success": True, "archived": True}
            return {"error": f"Unknown action: {action}"}

        elif tool_name == "call_external_api":
            if args.get("api") not in APPROVED_APIS: return {"error": f"API not approved: {args.get('api')}"}
            import httpx
            url = f"{APPROVED_APIS[args['api']]['base_url']}/{args.get('endpoint', '').lstrip('/')}"
            try:
                async with httpx.AsyncClient() as client:
                    resp = await client.get(url, params=args.get("params", {}), timeout=10.0)
                    return {"success": True, "data": resp.json() if resp.status_code == 200 else resp.text[:5000]} if resp.status_code == 200 else {"error": f"API error: {resp.status_code}"}
            except Exception as e:
                return {"error": f"Request failed: {e}"}

        elif tool_name == "web_search":
            if not args.get("query"): return {"error": "query required"}
            import httpx
            try:
                async with httpx.AsyncClient() as client:
                    resp = await client.get("https://api.duckduckgo.com/", params={"q": args["query"], "format": "json", "no_html": 1}, timeout=10.0)
                    if resp.status_code == 200:
                        data = resp.json()
                        results = []
                        if data.get("Abstract"): results.append({"title": data.get("Heading", args["query"]), "snippet": data.get("Abstract"), "url": data.get("AbstractURL")})
                        for t in data.get("RelatedTopics", [])[:4]:
                            if isinstance(t, dict) and t.get("Text"): results.append({"title": t.get("Text", "")[:100], "snippet": t.get("Text"), "url": t.get("FirstURL")})
                        return {"query": args["query"], "results": results}
            except Exception as e:
                return {"error": f"Web search failed: {e}"}

        elif tool_name == "browse_webpage":
            if not args.get("url"): return {"error": "url required"}
            import httpx
            from bs4 import BeautifulSoup
            try:
                async with httpx.AsyncClient() as client:
                    resp = await client.get(args["url"], timeout=10.0, follow_redirects=True)
                    if resp.status_code != 200: return {"error": f"HTTP {resp.status_code}"}
                    soup = BeautifulSoup(resp.text, "lxml")
                    for tag in soup(["script", "style", "nav", "footer"]): tag.decompose()
                    result = {"url": args["url"]}
                    ext = args.get("extract", "text")
                    if ext in ("text", "all"): result["text"] = soup.get_text(separator="\n", strip=True)[:args.get("max_length", 5000)]
                    if ext in ("links", "all"): result["links"] = [{"text": a.get_text(strip=True)[:100], "href": a["href"]} for a in soup.find_all("a", href=True)[:20]]
                    if ext in ("tables", "all"): result["tables"] = [[[td.get_text(strip=True) for td in tr.find_all(["td", "th"])] for tr in table.find_all("tr")[:20]] for table in soup.find_all("table")[:5]]
                    return result
            except Exception as e:
                return {"error": f"Failed to fetch: {e}"}

        elif tool_name == "long_term_memory":
            action = args.get("action")
            if action == "store":
                if not args.get("key") or not args.get("value"): return {"error": "key and value required"}
                existing = (await db.execute(select(AgentMemory).where(AgentMemory.user_id == user_id, AgentMemory.key == args["key"]))).scalar_one_or_none()
                if existing:
                    existing.value = args["value"]
                    existing.memory_type = args.get("memory_type", MemoryType.NOTE.value)
                    existing.updated_at = datetime.now()
                else:
                    db.add(AgentMemory(user_id=user_id, key=args["key"], value=args["value"], memory_type=args.get("memory_type", MemoryType.NOTE.value)))
                await db.commit()
                return {"success": True, "key": args["key"], "action": "stored"}
            elif action == "retrieve":
                if not args.get("key"): return {"error": "key required"}
                m = (await db.execute(select(AgentMemory).where(AgentMemory.user_id == user_id, AgentMemory.key == args["key"]))).scalar_one_or_none()
                if m:
                    m.access_count += 1
                    m.last_accessed_at = datetime.now()
                    await db.commit()
                    return {"key": m.key, "value": m.value, "type": m.memory_type, "created_at": m.created_at.isoformat()}
                return {"error": f"Memory '{args['key']}' not found"}
            elif action == "search":
                mems = (await db.execute(select(AgentMemory).where(AgentMemory.user_id == user_id).where(AgentMemory.key.ilike(f"%{args.get('query', '')}%") | AgentMemory.value.ilike(f"%{args.get('query', '')}%")).limit(10))).scalars().all()
                return {"query": args.get("query", ""), "memories": [{"key": m.key, "value": m.value[:200], "type": m.memory_type} for m in mems]}
            elif action == "list":
                mems = (await db.execute(select(AgentMemory).where(AgentMemory.user_id == user_id).order_by(AgentMemory.updated_at.desc()).limit(20))).scalars().all()
                return {"memories": [{"key": m.key, "type": m.memory_type, "updated": m.updated_at.isoformat()} for m in mems]}
            return {"error": f"Unknown action: {action}"}

        return {"error": f"Unknown tool: {tool_name}"}

    except Exception as e:
        logger.exception(f"Error executing tool {tool_name}")
        # Rollback the session to recover from any database errors
        try:
            await db.rollback()
        except Exception:
            pass  # Ignore rollback errors
        return {"error": str(e)}
