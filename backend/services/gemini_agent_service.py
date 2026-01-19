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
import ipaddress
from datetime import datetime, date
from typing import Optional, List, Dict, Any
from urllib.parse import urlparse


def _serialize_for_json(obj: Any) -> Any:
    """Recursively serialize an object for JSON storage, handling datetime and numpy objects."""
    import numpy as np

    if isinstance(obj, (datetime, date)):
        return obj.isoformat()
    elif isinstance(obj, (np.integer, np.int64, np.int32)):
        return int(obj)
    elif isinstance(obj, (np.floating, np.float64, np.float32)):
        return float(obj)
    elif isinstance(obj, np.ndarray):
        return obj.tolist()
    elif isinstance(obj, np.bool_):
        return bool(obj)
    elif isinstance(obj, dict):
        return {k: _serialize_for_json(v) for k, v in obj.items()}
    elif isinstance(obj, (list, tuple)):
        return [_serialize_for_json(item) for item in obj]
    return obj

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
# Ensure our logger outputs at INFO level
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger.setLevel(logging.INFO)
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


def _is_safe_url(url: str) -> tuple[bool, str]:
    """
    Validate URL for SSRF protection.

    Blocks:
    - Non HTTP/HTTPS schemes
    - Private/internal IP addresses
    - Cloud metadata endpoints
    - Localhost and loopback addresses

    Returns:
        Tuple of (is_safe, error_message)
    """
    try:
        parsed = urlparse(url)

        # Only allow http/https
        if parsed.scheme not in ("http", "https"):
            return False, f"Only HTTP/HTTPS URLs allowed, got: {parsed.scheme}"

        hostname = parsed.hostname
        if not hostname:
            return False, "Invalid URL: no hostname"

        # Block localhost and loopback
        if hostname.lower() in ("localhost", "127.0.0.1", "0.0.0.0", "::1", "[::1]"):
            return False, "Access to localhost is not allowed"

        # Try to parse as IP address to check for private ranges
        try:
            ip = ipaddress.ip_address(hostname)
            if ip.is_private:
                return False, "Access to private IP addresses is not allowed"
            if ip.is_loopback:
                return False, "Access to loopback addresses is not allowed"
            if ip.is_link_local:
                return False, "Access to link-local addresses is not allowed"
            if ip.is_multicast:
                return False, "Access to multicast addresses is not allowed"
            # Block cloud metadata endpoints
            if str(ip) in ("169.254.169.254", "100.100.100.200"):
                return False, "Access to cloud metadata endpoints is not allowed"
        except ValueError:
            # Not an IP address, it's a hostname - that's fine
            # But still check for obvious internal hostnames
            hostname_lower = hostname.lower()
            if hostname_lower.endswith(".internal") or hostname_lower.endswith(".local"):
                return False, "Access to internal hostnames is not allowed"

        return True, ""
    except Exception as e:
        return False, f"Invalid URL: {e}"

# System prompt for the research assistant
SYSTEM_PROMPT = """You are MAPtimize Assistant, an expert AI research assistant with FULL ACCESS to the user's research environment.

## Your Expertise
- Cell biology and microscopy analysis
- Microtubule-associated proteins (MAPs)
- Fluorescence microscopy interpretation
- Experimental data analysis

## Communication Style - FOR BIOLOGISTS

**IMPORTANT: Your users are biologists, not computer scientists. Communicate accordingly:**

1. **NEVER mention AI models by name** - Don't say "SAM 3", "DINOv2", "Gemini", "Qwen", etc.
2. **Use biological language** - Talk about "cells", "detection", "analysis", not "inference" or "embeddings"
3. **Focus on results, not methods** - Say "bylo detekováno 47 buněk" not "model detekoval 47 buněk"
4. **Avoid AI jargon** - No "neural network", "deep learning", "machine learning", "model", "AI"
5. **Be a helpful lab assistant** - Present yourself as a knowledgeable assistant, not as an AI

**Good examples:**
- ✅ "V tomto snímku bylo detekováno 47 buněk."
- ✅ "Analýza intenzity ukazuje průměrnou hodnotu 15.3."
- ✅ "Na základě podobnosti buněk jsem našel tyto shluky..."

**Bad examples:**
- ❌ "Model SAM 3 detekoval 47 buněk."
- ❌ "Pomocí DINOv2 embeddingů jsem analyzoval podobnost."
- ❌ "Jako AI model Gemini 3 mohu..."

## Internal Terminology (for your understanding, NOT for users)

**DETECTION vs SEGMENTATION:**

1. **Detection / Crops** = Bounding boxes around cells
   - Rectangular regions (x, y, width, height) containing cells
   - Stored as `cell_crops` in database
   - Use `get_cell_detection_results` to retrieve data

2. **Segmentation** = Per-pixel polygon masks (more detailed than detection)
   - Precise polygon boundaries of cells
   - Stored as `segmentation_masks` (per cell) and `fov_segmentation_masks` (whole image)
   - Use `get_segmentation_masks` to retrieve polygon data
   - Use `render_segmentation_overlay` to VISUALIZE masks as images
   - Includes IoU score (quality), area in pixels, creation method

**AUTONOMOUS VISUALIZATION:**
When user asks about segmentation, masks, or boundaries:
1. First check if masks exist using `get_segmentation_masks`
2. Then AUTOMATICALLY render visualization using `render_segmentation_overlay`
3. Display the resulting image using markdown: `![Segmentation](url)`
Don't just tell the user about data - SHOW them visualizations!

**When talking to users:**
- Say "detekované buňky" or "výřezy buněk" - NOT "cell crops"
- Say "automatická detekce" - NOT "SAM 3 detection"
- Say "analýza podobnosti" - NOT "embedding similarity"

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
- **get_cell_detection_results**: Get cell detection results (bounding boxes) for an image
- **get_segmentation_masks**: Get SAM segmentation polygons for cells or FOV images
- **render_segmentation_overlay**: Render mask visualization on image (returns displayable image URL)

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

### 3. USE CODE FOR DATA ANALYSIS & VISUALIZATION
For ANY calculation or visualization, use `execute_python_code`:
```python
import numpy as np
import pandas as pd
import matplotlib.pyplot as plt

# Example: Create a histogram of mean_intensity
data = [14.2, 18.5, 9.3, 22.1, 11.7]  # From query_database
plt.figure(figsize=(10, 6))
plt.hist(data, bins=20, color='steelblue', edgecolor='white')
plt.xlabel('Mean Intensity')
plt.ylabel('Frequency')
plt.title('Distribution of Cell Mean Intensity')
plt.show()
```

**IMPORTANT:** When the code returns plots (in `plots` array), INCLUDE THEM in your response using the `plots_markdown` array:
```
![Plot 1](/uploads/temp/plot_xxx.png)
```
The plots are saved as files and the URLs are ready to use - just copy from `plots_markdown`.

**Data Analysis Workflow:**
1. Use `query_database` to get raw data from the database
2. Use `execute_python_code` to analyze data and create visualizations
3. Display any returned plots using the markdown from `plots_markdown` array

### 4. CITE SOURCES
- Reference documents as [Doc: "filename" p.X]
- Reference images as [FOV: "filename" from "experiment"]

### 5. REMEMBER IMPORTANT THINGS
Use `long_term_memory` to store key findings, user preferences, or project context.

## Data Sources - Choose Wisely!

**TWO SEPARATE DATA SOURCES:**

1. **EXPERIMENTS** = User's microscopy data (images, cells, measurements)
   - Tools: `list_experiments`, `list_images`, `get_experiment_stats`, `get_cell_detection_results`, `query_database`
   - When user asks: "show PRC1 images", "my data", "experiment results", protein names
   - First find the experiment by name, then get its images/data

2. **DOCUMENTS** = Uploaded PDFs, papers, protocols
   - Tools: `semantic_search`, `search_documents`, `get_document_content`
   - When user asks: "what does the paper say", "methods in NAEX", "search documents"
   - Vision RAG: pages are read as images by AI

**AUTONOMOUS DECISION MAKING:**
- If user mentions a protein name (PRC1, HMMR, etc.) → likely asking about their EXPERIMENT data
- If user mentions a document name or "paper" → use document search
- When unsure → check both: first list_experiments to see if it matches, then search documents

**DISPLAYING IMAGES:**
When tools return `thumbnail_url`, display images using CORRECT markdown format:
```
![filename](/api/images/123/file?type=thumbnail)
```

**CRITICAL: For multiple images, put them ALL ON THE SAME LINE separated by spaces:**
```
![img1](/url1) ![img2](/url2) ![img3](/url3)
```
This ensures they display in a 3-column grid. DO NOT put each image on a new line!

NOT: `![name](thumbnail_url: /api/...)` - this is WRONG!
Just use the URL directly without "thumbnail_url:" prefix.

**FILE DOWNLOADS (exports):**
When user asks to export/download data:
1. Use the `export_data` tool with appropriate parameters
2. The tool returns `{"download_url": "/uploads/exports/filename.xlsx", ...}`
3. Present the download link using EXACTLY the URL from `download_url`:
```markdown
[Download filename.xlsx](/uploads/exports/filename.xlsx)
```

**CRITICAL:** NEVER invent or guess export URLs! Always use the `download_url` returned by the tool.
WRONG: `/api/experiments/9/export?...` - This URL format does NOT exist!
CORRECT: `/uploads/exports/cell_crops_exp9_20260119_123456.xlsx` - Use the actual URL from tool result!

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
        "description": "Get cell detection results for an image. Returns bounding boxes (crops) with coordinates, confidence scores, and cell count. Note: This is DETECTION (bounding boxes), not SEGMENTATION (per-pixel masks).",
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
        "description": "Execute Python code in secure sandbox. Available: numpy, pandas, scipy, matplotlib, seaborn, statistics. Returns stdout, return value, and plots as URLs (saved to temp folder).",
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

    # === SEGMENTATION ===
    {
        "name": "get_segmentation_masks",
        "description": "Get SAM segmentation masks (per-pixel polygons) for cells or FOV images. Returns polygon points, IoU score, and area.",
        "parameters": {
            "type": "object",
            "properties": {
                "crop_ids": {"type": "array", "items": {"type": "integer"}, "description": "List of cell crop IDs to get masks for"},
                "image_id": {"type": "integer", "description": "FOV image ID to get full-image mask"},
                "include_stats": {"type": "boolean", "description": "Include mask statistics (area, IoU)"}
            }
        }
    },
    {
        "name": "render_segmentation_overlay",
        "description": "Render segmentation mask overlaid on image. Returns image URL that can be displayed. Use this to VISUALIZE masks.",
        "parameters": {
            "type": "object",
            "properties": {
                "image_id": {"type": "integer", "description": "FOV image ID"},
                "crop_id": {"type": "integer", "description": "Cell crop ID (renders mask on crop image)"},
                "show_polygon": {"type": "boolean", "description": "Draw polygon outline (default true)"},
                "show_fill": {"type": "boolean", "description": "Fill polygon with semi-transparent color (default true)"},
                "color": {"type": "string", "description": "Color name: red, green, blue, yellow, cyan, magenta (default green)"}
            }
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

    max_iterations = 20
    for iteration in range(max_iterations):
        try:
            # After many tool calls, hint to generate a response
            current_tools = gemini_tools
            tool_mode = "AUTO"
            if len(tool_calls_log) >= 15:
                # Disable tools after 15 calls to force text generation
                current_tools = None
                tool_mode = "NONE"
                logger.info(f"Iteration {iteration}: Disabling tools after {len(tool_calls_log)} calls to force response")

            logger.info(f"Gemini iteration {iteration} starting with {len(messages)} messages, {len(tool_calls_log)} tool calls...")

            config_kwargs = {
                "system_instruction": SYSTEM_PROMPT,
                "temperature": 0.7,
            }
            if current_tools:
                config_kwargs["tools"] = current_tools
                config_kwargs["tool_config"] = types.ToolConfig(function_calling_config=types.FunctionCallingConfig(mode=tool_mode))

            response = client.models.generate_content(
                model="gemini-3-flash-preview",
                contents=messages,
                config=types.GenerateContentConfig(**config_kwargs)
            )
            logger.info(f"Gemini iteration {iteration} completed - candidates: {len(response.candidates) if response.candidates else 0}")

            # Log response details for debugging
            if response.candidates:
                candidate = response.candidates[0]
                logger.info(f"  Candidate finish_reason: {candidate.finish_reason if hasattr(candidate, 'finish_reason') else 'N/A'}")
                if candidate.content and candidate.content.parts:
                    for i, part in enumerate(candidate.content.parts):
                        if hasattr(part, 'text') and part.text:
                            logger.info(f"  Part {i}: TEXT ({len(part.text)} chars)")
                        elif hasattr(part, 'function_call') and part.function_call:
                            logger.info(f"  Part {i}: FUNCTION_CALL ({part.function_call.name})")
                        else:
                            logger.info(f"  Part {i}: OTHER ({type(part)})")
            else:
                logger.warning(f"  No candidates in response!")
                if hasattr(response, 'prompt_feedback'):
                    logger.warning(f"  Prompt feedback: {response.prompt_feedback}")

        except Exception as e:
            logger.exception(f"Gemini API error at iteration {iteration}: {e}")
            return {"content": f"Error calling AI service: {str(e)}", "citations": citations, "image_refs": image_refs, "tool_calls": tool_calls_log}

        logger.debug(f"Gemini iteration {iteration}")

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
                logger.info(f"Executing tool: {tool_name} with args: {tool_args}")

                try:
                    tool_result = await execute_tool(tool_name, tool_args, user_id, db)
                    logger.info(f"Tool {tool_name} completed successfully")
                except Exception as tool_error:
                    logger.exception(f"Tool execution error for {tool_name}: {tool_error}")
                    tool_result = {"error": f"Tool execution failed: {str(tool_error)}"}

                # Serialize result to handle datetime and numpy objects before storing in JSONB
                try:
                    serialized_result = "..." if tool_name == "get_document_content" else _serialize_for_json(tool_result)
                except Exception as ser_error:
                    logger.exception(f"Serialization error for {tool_name} result: {ser_error}")
                    serialized_result = {"error": f"Serialization failed: {str(ser_error)}"}
                tool_calls_log.append({"tool": tool_name, "args": _serialize_for_json(tool_args), "result": serialized_result})

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

                # Append the original model content to preserve thought_signature (required for Gemini 3)
                messages.append(response.candidates[0].content)
                logger.info(f"Appended model content with function_call to messages")

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
                    logger.info(f"Appended document content with {len(response_parts)} parts (vision mode)")
                else:
                    # Function response - create FunctionResponse Part WITHOUT role="user"
                    # The google-genai SDK expects function responses without explicit role
                    serialized_response = _serialize_for_json(tool_result)
                    logger.info(f"Sending function_response for {tool_name}, response keys: {list(serialized_response.keys()) if isinstance(serialized_response, dict) else 'non-dict'}")
                    function_response_part = types.Part(function_response=types.FunctionResponse(
                        name=tool_name,
                        response=serialized_response
                    ))
                    # Note: Trying without explicit role - let the SDK handle it
                    messages.append(types.Content(parts=[function_response_part]))
                    logger.info(f"Appended function_response to messages (no explicit role)")
                continue

            text_parts = [p.text for p in parts if hasattr(p, 'text') and p.text]
            if text_parts:
                logger.info(f"Returning text response with {len(text_parts)} parts")
                return {"content": "\n".join(text_parts), "citations": citations, "image_refs": image_refs, "tool_calls": tool_calls_log}

        # Try response.text as fallback
        if response.text:
            logger.info(f"Returning response.text fallback ({len(response.text)} chars)")
            return {"content": response.text, "citations": citations, "image_refs": image_refs, "tool_calls": tool_calls_log}

        # If we get here without text or function_call after having tool calls,
        # Gemini 3 sometimes returns empty response - retry once more
        if tool_calls_log and iteration < max_iterations - 1:
            logger.warning(f"Iteration {iteration}: No text after tool call, retrying...")
            # Log what parts we received for debugging
            if response.candidates and response.candidates[0].content.parts:
                for i, part in enumerate(response.candidates[0].content.parts):
                    logger.warning(f"  Part {i} type: {type(part).__name__}, attrs: {[a for a in dir(part) if not a.startswith('_')]}")
            continue  # Try next iteration instead of breaking

        # If we get here without text or function_call, something is wrong
        logger.warning(f"Iteration {iteration}: No text and no function_call found, breaking loop")
        logger.warning(f"  response.candidates: {response.candidates}")
        logger.warning(f"  response.text: {response.text if hasattr(response, 'text') else 'N/A'}")
        break

    # If we exhausted iterations but have tool results, generate a smart fallback response
    if tool_calls_log:
        logger.warning(f"Agent loop exhausted with {len(tool_calls_log)} tool calls but no text - generating fallback")

        # Extract useful data from tool results for display
        fallback_parts = ["Zde jsou výsledky mé analýzy:\n"]
        images_found = []
        stats_found = []

        for tc in tool_calls_log:
            result = tc.get("result", {})
            if isinstance(result, dict):
                # Extract images from render_segmentation_overlay
                if tc["tool"] == "render_segmentation_overlay" and result.get("image_markdown"):
                    images_found.append(result["image_markdown"])
                # Extract experiment stats
                elif tc["tool"] == "get_experiment_stats":
                    stats_found.append(f"- Experiment: {result.get('name', 'N/A')}, obrázků: {result.get('image_count', 0)}, buněk: {result.get('cell_count', 0)}")
                # Extract segmentation info
                elif tc["tool"] == "get_segmentation_masks":
                    if result.get("masks_found"):
                        stats_found.append(f"- Nalezeno {result.get('masks_found', 0)} segmentačních masek")
                # Extract cell images from get_cell_detection_results
                elif tc["tool"] == "get_cell_detection_results" and result.get("crops"):
                    for crop in result["crops"][:6]:
                        if crop.get("thumbnail_url"):
                            images_found.append(f"![Cell {crop['id']}]({crop['thumbnail_url']})")

        if stats_found:
            fallback_parts.append("\n".join(stats_found))

        if images_found:
            fallback_parts.append("\n\n**Vizualizace:**\n" + " ".join(images_found[:9]))  # Max 9 images

        if not stats_found and not images_found:
            tool_summary = ", ".join([tc["tool"] for tc in tool_calls_log])
            fallback_parts = [f"Provedl jsem následující akce: {tool_summary}. Zkuste prosím dotaz zopakovat."]

        fallback_content = "\n".join(fallback_parts)
        return {"content": fallback_content, "citations": citations, "image_refs": image_refs, "tool_calls": tool_calls_log}

    logger.error(f"Agent loop exhausted after {max_iterations} iterations or early break, tool_calls: {len(tool_calls_log)}")
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
            # Use correct column names: detection_confidence, bbox_w, bbox_h
            crops = (await db.execute(select(CellCrop).where(CellCrop.image_id == args["image_id"]).order_by(CellCrop.detection_confidence.desc()))).scalars().all()
            result = {"image_id": args["image_id"], "filename": img.original_filename, "cell_count": len(crops), "detection_summary": {"total": len(crops), "avg_confidence": sum(c.detection_confidence or 0 for c in crops) / len(crops) if crops else 0, "avg_area": sum((c.bbox_w or 0) * (c.bbox_h or 0) for c in crops) / len(crops) if crops else 0}}
            if args.get("include_crops"):
                result["crops"] = [{"id": c.id, "bbox": {"x": c.bbox_x, "y": c.bbox_y, "w": c.bbox_w, "h": c.bbox_h}, "confidence": c.detection_confidence, "thumbnail_url": f"/api/images/crops/{c.id}/image"} for c in crops[:50]]
            return result

        elif tool_name == "execute_python_code":
            if not args.get("code"): return {"error": "code required"}
            from services.code_execution_service import execute_python_code
            result = await execute_python_code(code=args["code"], timeout_seconds=args.get("timeout_seconds", 30))
            # Add markdown-ready plot strings for easy inclusion in response
            if result.get("plots"):
                result["plots_markdown"] = [f"![Plot {i+1}]({plot})" for i, plot in enumerate(result["plots"])]
                result["display_instruction"] = "INCLUDE THESE PLOTS IN YOUR RESPONSE - just copy the markdown from plots_markdown array"
            return result

        elif tool_name == "query_database":
            if not args.get("query"): return {"error": "query required"}
            query_str = args["query"].strip()
            query_upper = query_str.upper()
            try:
                parsed = sqlparse.parse(query_str)
                if not parsed or parsed[0].get_type() != "SELECT": return {"error": "Only SELECT allowed"}

                # Check for forbidden keywords (SQL injection prevention)
                forbidden = ["INSERT", "UPDATE", "DELETE", "DROP", "CREATE", "ALTER", "TRUNCATE", "GRANT", "REVOKE", "--", "/*", "*/"]
                for kw in forbidden:
                    if kw in query_upper: return {"error": f"Forbidden keyword: {kw}"}

                # Block multiple statements (semicolon injection)
                if query_str.count(";") > 0:
                    return {"error": "Multiple statements not allowed (semicolons forbidden)"}

                # Block subqueries that could access other tables
                if "(" in query_upper and "SELECT" in query_upper.split("(", 1)[-1]:
                    return {"error": "Subqueries not allowed"}

                # Block UNION/INTERSECT/EXCEPT that could access other data
                if any(kw in query_upper for kw in ["UNION", "INTERSECT", "EXCEPT"]):
                    return {"error": "UNION/INTERSECT/EXCEPT not allowed"}

                # Validate table names against whitelist
                found_tables = set()
                for token in parsed[0].flatten():
                    if token.ttype is None:
                        val_lower = token.value.lower()
                        # Skip SQL keywords
                        if val_lower in ("select", "from", "where", "and", "or", "as", "on", "join", "left", "right", "inner", "outer", "group", "by", "order", "limit", "count", "sum", "avg", "min", "max", "distinct", "asc", "desc", "true", "false", "null", "is", "not", "in", "like", "between", "case", "when", "then", "else", "end", "having", "offset"):
                            continue
                        # Skip if it contains a dot (column reference like table.column)
                        if "." in token.value:
                            table_part = val_lower.split(".")[0]
                            if table_part in ALLOWED_SQL_TABLES:
                                found_tables.add(table_part)
                            continue
                        # Check if it's a table name
                        if val_lower in ALLOWED_SQL_TABLES:
                            found_tables.add(val_lower)
                        elif val_lower in ("users", "passwords", "secrets", "tokens", "sessions"):
                            return {"error": f"Access denied to table: {token.value}"}

            except Exception as e:
                return {"error": f"Parse error: {e}"}

            try:
                # Use parameterized queries with :user_id placeholder
                # Rebuild query to add user_id filter safely
                limit_val = min(args.get('limit', 100), 1000)

                # Determine which table needs user_id filter
                needs_exp_filter = "experiments" in query_upper and "USER_ID" not in query_upper
                needs_doc_filter = "rag_documents" in query_upper and "USER_ID" not in query_upper

                if needs_exp_filter:
                    if "WHERE" in query_upper:
                        base_q = query_str.upper().replace("WHERE", "WHERE experiments.user_id = :user_id AND", 1)
                        base_q = query_str[:query_str.upper().find("WHERE")] + base_q[query_str.upper().find("WHERE"):]
                    else:
                        base_q = query_str.rstrip() + " WHERE experiments.user_id = :user_id"
                elif needs_doc_filter:
                    if "WHERE" in query_upper:
                        base_q = query_str.upper().replace("WHERE", "WHERE rag_documents.user_id = :user_id AND", 1)
                        base_q = query_str[:query_str.upper().find("WHERE")] + base_q[query_str.upper().find("WHERE"):]
                    else:
                        base_q = query_str.rstrip() + " WHERE rag_documents.user_id = :user_id"
                else:
                    base_q = query_str

                # Add LIMIT if missing
                final_q = base_q if "LIMIT" in query_upper else f"{base_q} LIMIT :limit_val"

                # Execute with parameters (prevents SQL injection)
                result = await db.execute(
                    text(final_q),
                    {"user_id": user_id, "limit_val": limit_val}
                )
                rows = result.fetchall()
                cols = list(result.keys())
                return {"success": True, "columns": cols, "rows": [dict(zip(cols, r)) for r in rows], "row_count": len(rows)}
            except Exception as e:
                logger.warning(f"Query execution error for user {user_id}: {e}")
                # Rollback to recover from failed transaction
                try:
                    await db.rollback()
                except Exception:
                    pass
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
                # Use correct column names: bbox_w, bbox_h, detection_confidence
                row = (await db.execute(select(Experiment.name, func.count(CellCrop.id).label("cc"), func.avg(CellCrop.bbox_w * CellCrop.bbox_h).label("area"), func.avg(CellCrop.detection_confidence).label("conf")).outerjoin(Image, Experiment.id == Image.experiment_id).outerjoin(CellCrop, Image.id == CellCrop.image_id).where(Experiment.id == eid, Experiment.user_id == user_id).group_by(Experiment.id))).first()
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
                api_name = args.get('api', 'unknown')
                logger.warning(f"External API call to {api_name} failed: {e}")
                return {"error": f"Request to {api_name} failed: {e}"}

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
                    else:
                        return {"error": f"Web search returned status {resp.status_code}"}
            except Exception as e:
                return {"error": f"Web search failed: {e}"}

        elif tool_name == "browse_webpage":
            if not args.get("url"): return {"error": "url required"}

            # SSRF protection - validate URL before fetching
            is_safe, error_msg = _is_safe_url(args["url"])
            if not is_safe:
                logger.warning(f"SSRF attempt blocked for user {user_id}: {args['url']} - {error_msg}")
                return {"error": error_msg}

            import httpx
            from bs4 import BeautifulSoup
            try:
                async with httpx.AsyncClient() as client:
                    # Disable redirects initially to validate each hop
                    resp = await client.get(args["url"], timeout=10.0, follow_redirects=False)

                    # Handle redirects manually with SSRF check on each hop
                    redirect_count = 0
                    while resp.status_code in (301, 302, 303, 307, 308) and redirect_count < 5:
                        redirect_url = resp.headers.get("location")
                        if not redirect_url:
                            return {"error": "Redirect without location header"}

                        # Validate redirect URL for SSRF
                        is_safe, error_msg = _is_safe_url(redirect_url)
                        if not is_safe:
                            logger.warning(f"SSRF redirect blocked for user {user_id}: {redirect_url}")
                            return {"error": f"Redirect blocked: {error_msg}"}

                        resp = await client.get(redirect_url, timeout=10.0, follow_redirects=False)
                        redirect_count += 1

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

        elif tool_name == "get_segmentation_masks":
            from models.segmentation import SegmentationMask, FOVSegmentationMask
            result = {}

            # Get masks for specific cell crops
            if args.get("crop_ids"):
                crop_ids = args["crop_ids"]
                masks_result = await db.execute(
                    select(SegmentationMask)
                    .where(SegmentationMask.cell_crop_id.in_(crop_ids))
                )
                masks = masks_result.scalars().all()
                result["cell_masks"] = [
                    {
                        "crop_id": m.cell_crop_id,
                        "polygon_points": m.polygon_points,
                        "area_pixels": m.area_pixels,
                        "iou_score": m.iou_score,
                        "creation_method": m.creation_method,
                        "prompt_count": m.prompt_count,
                    }
                    for m in masks
                ]
                result["masks_found"] = len(masks)
                result["masks_missing"] = len(crop_ids) - len(masks)

            # Get FOV-level mask
            if args.get("image_id"):
                # Verify user owns the image
                img_check = await db.execute(
                    select(Image).join(Experiment).where(
                        Image.id == args["image_id"],
                        Experiment.user_id == user_id
                    )
                )
                if img_check.scalar_one_or_none():
                    fov_result = await db.execute(
                        select(FOVSegmentationMask).where(FOVSegmentationMask.image_id == args["image_id"])
                    )
                    fov_mask = fov_result.scalar_one_or_none()
                    if fov_mask:
                        result["fov_mask"] = {
                            "image_id": fov_mask.image_id,
                            "polygon_points": fov_mask.polygon_points,
                            "area_pixels": fov_mask.area_pixels,
                            "iou_score": fov_mask.iou_score,
                            "creation_method": fov_mask.creation_method,
                        }
                    else:
                        result["fov_mask"] = None
                        result["fov_mask_status"] = "No FOV mask saved for this image"
                else:
                    result["error"] = "Image not found or access denied"

            if not args.get("crop_ids") and not args.get("image_id"):
                return {"error": "Provide crop_ids or image_id"}

            return result

        elif tool_name == "render_segmentation_overlay":
            import uuid
            from datetime import datetime
            from pathlib import Path
            from PIL import Image as PILImage, ImageDraw
            import numpy as np
            from models.segmentation import SegmentationMask, FOVSegmentationMask
            from config import get_settings

            settings = get_settings()
            TEMP_DIR = Path(settings.upload_dir) / "temp"
            TEMP_DIR.mkdir(parents=True, exist_ok=True)

            # Color mapping
            colors = {
                "red": (255, 0, 0), "green": (0, 255, 0), "blue": (0, 0, 255),
                "yellow": (255, 255, 0), "cyan": (0, 255, 255), "magenta": (255, 0, 255),
            }
            color = colors.get(args.get("color", "green"), (0, 255, 0))
            show_polygon = args.get("show_polygon", True)
            show_fill = args.get("show_fill", True)

            if args.get("crop_id"):
                # Render crop with mask
                crop_result = await db.execute(
                    select(CellCrop).options(selectinload(CellCrop.image).selectinload(Image.experiment))
                    .where(CellCrop.id == args["crop_id"])
                )
                crop = crop_result.scalar_one_or_none()
                if not crop or crop.image.experiment.user_id != user_id:
                    return {"error": "Crop not found or access denied"}

                # Get mask
                mask_result = await db.execute(
                    select(SegmentationMask).where(SegmentationMask.cell_crop_id == args["crop_id"])
                )
                mask = mask_result.scalar_one_or_none()
                if not mask:
                    return {"error": "No segmentation mask found for this crop", "crop_id": args["crop_id"]}

                # Load crop image
                if not crop.mip_path or not Path(crop.mip_path).exists():
                    return {"error": "Crop image file not found"}

                img = PILImage.open(crop.mip_path).convert("RGBA")

                # Translate polygon to crop coordinates (mask is in FOV coords)
                polygon = [(p[0] - crop.bbox_x, p[1] - crop.bbox_y) for p in mask.polygon_points]

            elif args.get("image_id"):
                # Render FOV with mask
                img_result = await db.execute(
                    select(Image).join(Experiment).where(
                        Image.id == args["image_id"], Experiment.user_id == user_id
                    )
                )
                image = img_result.scalar_one_or_none()
                if not image:
                    return {"error": "Image not found or access denied"}

                # Get FOV mask
                mask_result = await db.execute(
                    select(FOVSegmentationMask).where(FOVSegmentationMask.image_id == args["image_id"])
                )
                mask = mask_result.scalar_one_or_none()
                if not mask:
                    return {"error": "No FOV segmentation mask found for this image", "image_id": args["image_id"]}

                # Load image (prefer MIP projection)
                img_path = image.mip_path or image.file_path
                if not img_path or not Path(img_path).exists():
                    return {"error": "Image file not found"}

                img = PILImage.open(img_path).convert("RGBA")
                polygon = mask.polygon_points
            else:
                return {"error": "Provide image_id or crop_id"}

            # Create overlay
            overlay = PILImage.new("RGBA", img.size, (0, 0, 0, 0))
            draw = ImageDraw.Draw(overlay)

            # Handle multiple polygons (FOV masks can have multiple)
            polygons_to_draw = []
            if polygon and isinstance(polygon[0], list) and len(polygon[0]) > 0:
                if isinstance(polygon[0][0], list):
                    # Multiple polygons: [[[x,y],...], [[x,y],...]]
                    polygons_to_draw = [[(p[0], p[1]) for p in poly] for poly in polygon]
                else:
                    # Single polygon: [[x,y], [x,y], ...]
                    polygons_to_draw = [[(p[0], p[1]) for p in polygon]]

            for poly_coords in polygons_to_draw:
                if len(poly_coords) < 3:
                    continue
                if show_fill:
                    draw.polygon(poly_coords, fill=(*color, 80))  # Semi-transparent fill
                if show_polygon:
                    draw.polygon(poly_coords, outline=(*color, 255), width=2)

            # Composite
            result_img = PILImage.alpha_composite(img, overlay).convert("RGB")

            # Save to temp
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            unique_id = uuid.uuid4().hex[:8]
            filename = f"segmentation_{timestamp}_{unique_id}.png"
            filepath = TEMP_DIR / filename
            result_img.save(filepath, "PNG")

            url = f"/uploads/temp/{filename}"
            return {
                "success": True,
                "image_url": url,
                "image_markdown": f"![Segmentation]({url})",
                "mask_area_pixels": mask.area_pixels,
                "mask_iou_score": mask.iou_score,
            }

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
        except Exception as rollback_error:
            # Log rollback errors instead of silently ignoring
            logger.error(f"Rollback failed after tool error in {tool_name}: {rollback_error}")
        return {"error": str(e)}
