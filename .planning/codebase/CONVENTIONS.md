# Coding Conventions

**Analysis Date:** 2026-01-29

## Naming Patterns

**Files:**
- TypeScript/React files: `camelCase.ts`, `PascalCase.tsx` for components
- Python files: `snake_case.py`
- Page Object Models: `PascalCasePage.ts` (e.g., `AuthPage.ts`)
- Config files: lowercase with hyphens (e.g., `playwright.config.ts`)

**Functions:**
- TypeScript: `camelCase` for regular functions and async functions
- React hooks: `useXxx` pattern (e.g., `useBboxInteraction`, `useAuthStore`)
- TypeScript functions in libs: `camelCase` (e.g., `getHandlePositions`, `findBboxAtPosition`)
- Python: `snake_case` for functions and methods (e.g., `escape_sql_wildcards`, `get_user_or_404`)
- Async functions: `async def function_name()` in Python

**Variables:**
- TypeScript: `camelCase` for all variables (e.g., `imageWidth`, `editorState`, `drawingBbox`)
- React state: `camelCase` with descriptive names (e.g., `isLoading`, `errorMessage`, `copyState`)
- Python: `snake_case` for all variables (e.g., `query_embedding`, `user_id`, `file_size`)
- Constants: `UPPER_SNAKE_CASE` in both languages (e.g., `MIN_BBOX_SIZE`, `HANDLE_SIZE`)

**Types:**
- TypeScript interfaces: `PascalCase` with optional prefix/suffix (e.g., `AuthState`, `ChatMessage`, `ApiError`)
- TypeScript types: `PascalCase` (e.g., `DisplayMode`, `UmapType`, `ImageStatus`)
- Python type hints: Use full type paths from `typing` module (e.g., `Optional[str]`, `List[dict]`)
- Pydantic models: `PascalCase` (canonical source for request/response schemas)

## Code Style

**Formatting:**
- No explicit linting/formatting tool detected in frontend ESLint config
- TypeScript uses `strict: true` in tsconfig.json
- Next.js eslint-config-next applied
- Python: No black/flake8 config detected in pyproject.toml

**Linting:**
- TypeScript/Next.js: `eslint` (eslint-config-next)
  - Run: `npm run lint`
- Python: No explicit linter configured (pytest only)

**Line Length:**
- TypeScript: Long lines observed (500+ chars in api.ts comments/paths)
- Python: No enforced line length detected

## Import Organization

**Order (TypeScript):**
1. React imports: `import { useState, useRef } from "react"`
2. Next.js imports: `import { useRouter } from "next/navigation"`
3. i18n: `import { useTranslations } from "next-intl"`
4. Store imports: `import { useChatStore } from "@/stores/chatStore"`
5. Type imports: `import type { ChatMessage } from "@/lib/api"`
6. Library imports: `import clsx from "clsx"`, `import ReactMarkdown from "react-markdown"`
7. Local imports: `import { api, User } from "@/lib/api"`
8. Utils: `import { processImageUrl } from "@/lib/utils"`
9. Components: `import { MarkdownErrorBoundary } from "@/components/ui/ErrorBoundary"`
10. Styles: `import "katex/dist/katex.min.css"`

**Order (Python):**
1. Standard library: `import logging`, `import os`, `from datetime import datetime`
2. Third-party: `from fastapi import APIRouter`, `from sqlalchemy import func`
3. Local imports: `from database import get_db`, `from models.user import User`

**Path Aliases:**
- TypeScript: `@/*` → current directory (configured in tsconfig.json)
  - `@/stores/` → state management stores
  - `@/lib/` → utility libraries and helpers
  - `@/components/` → React components
  - `@/models/` → data models (Python)
  - `@/services/` → business logic services (Python)

## Error Handling

**TypeScript/React Patterns:**
- Try-catch with explicit error typing: `catch (err) { console.error("message:", err); }`
- API errors unwrapped: `throw new Error(errorDetail)` with user-friendly messages
- Component error boundaries: `<MarkdownErrorBoundary>` for rendering errors
- Validation errors handled at form level (HTML5 validation + field checks)
- Network errors detected and handled: `if (networkError instanceof TypeError)`
- Load states: `isLoading` boolean state with explicit checks before rendering

**Python/FastAPI Patterns:**
- HTTPException for API errors: `raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="...")`
- Custom exceptions: Define exception class extending `Exception` (e.g., `RAGServiceError`)
- Logging errors: `logger.warning()`, `logger.error()` before raising exceptions
- Async safety: Use `try-except` in async functions, return sensible defaults in searches
- Database errors: Check `scalar_one_or_none()` result before using, raise 404 if None

## Logging

**Framework:** Python uses `logging` module, TypeScript uses `console` methods

**Python Patterns:**
- Module-level logger: `logger = logging.getLogger(__name__)`
- Info level for actions: `logger.info(f"Admin {current_admin.email} fetched system stats")`
- Warning for auth issues: `logger.warning(f"Admin attempted to access non-existent user (id={user_id})")`
- Error with context: `logger.error(f"Failed to parse response: {error}")`

**TypeScript Patterns:**
- Console error for API failures: `console.error(\`[API] Network error calling ${endpoint}:\`, networkError)`
- Logging sanitization: `sanitizeUrlForLogging()` utility for sensitive URLs
- Conditional logging: Only log in development or on errors
- Avoid logging sensitive data: Check paths with startsWith validation (`sanitizeUrlForLogging`)

## Comments

**When to Comment:**
- Complex algorithms: e.g., Canvas rendering logic in `ImageEditorCanvas.tsx`
- API behavior documentation: e.g., two-phase upload workflow, token security notes
- Non-obvious state transitions: e.g., bbox interaction state machine in `useBboxInteraction`
- Configuration requirements: e.g., "MUST use /api/ prefix in frontend URLs"
- Important gotchas: e.g., "Using ?? instead of || because empty string is valid"

**JSDoc/TSDoc:**
- Function parameters documented in JSDoc: e.g., `/**\n * Search documents using vector similarity.\n * @param query User's search query\n * @param limit Maximum results\n */`
- Interface properties documented: `/** Whether to include extracted text content */`
- Type imports for re-exports: `// Re-export from canonical location to avoid DRY violation`
- Component props documented: `interface Props { /** Callback when image is ready */ onImageReady?: () => void }`

## Function Design

**Size:** Mix of small utility functions (5-10 lines) and larger functions (50-100 lines)

**Parameters:**
- Named parameters preferred in TypeScript (interfaces for multiple params)
- Optional params with trailing `= defaultValue`
- Destructuring in function signatures: `async function ({ imageIds, detectCells = true })`
- Python uses type hints: `async def function(param: Type, optional: Optional[Type] = None)`

**Return Values:**
- TypeScript API functions return typed promises: `Promise<Experiment[]>`
- Python async functions use type hints: `async def function() -> List[dict]`
- Custom response types defined as interfaces/Pydantic models
- Nullable returns documented in JSDoc: `Returns: string | null`

## Module Design

**Exports:**
- TypeScript: Explicit named exports and default exports
- API client: Singleton instance exported: `export const api = new ApiClient()`
- Services: Classes exported by default (Python) or as functions (TypeScript)
- Types: Always exported as named exports at module bottom

**Barrel Files:**
- Usage: Index files re-export Page Objects: `export { AuthPage } from "./AuthPage"`
- Location: `frontend/e2e/pages/index.ts` centralizes test page imports
- Location: `frontend/components/*/index.ts` not always used (direct imports preferred)

**Error Boundary Pattern:**
- React: `<MarkdownErrorBoundary>` wraps risky content (markdown rendering)
- Usage: Prevents single component failure from crashing entire page

---

*Convention analysis: 2026-01-29*
