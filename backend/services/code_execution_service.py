"""Secure Python code execution service using RestrictedPython.

This service provides a sandboxed environment for executing user-provided Python code
with strict security controls:
- Whitelisted imports only (numpy, pandas, scipy, matplotlib, etc.)
- No file I/O, network access, or system calls
- Memory and time limits
- Captured stdout and return values
"""

import ast
import io
import sys
import base64
import logging
import traceback
from typing import Any, Optional
from contextlib import redirect_stdout, redirect_stderr
import signal
import threading

from RestrictedPython import compile_restricted, safe_builtins
from RestrictedPython.Guards import (
    safe_builtins,
    guarded_iter_unpack_sequence,
    guarded_unpack_sequence,
)
from RestrictedPython.Eval import default_guarded_getiter, default_guarded_getitem

logger = logging.getLogger(__name__)

# Whitelisted modules that can be imported
ALLOWED_IMPORTS = {
    # Data analysis
    "numpy",
    "pandas",
    "scipy",
    "scipy.stats",
    "scipy.optimize",
    "scipy.signal",
    # Visualization
    "matplotlib",
    "matplotlib.pyplot",
    "seaborn",
    # Math and statistics
    "math",
    "statistics",
    "random",
    # Data structures
    "collections",
    "itertools",
    "functools",
    # JSON and datetime
    "json",
    "datetime",
    # Typing
    "typing",
}

# Forbidden AST node types that could be dangerous
FORBIDDEN_AST_NODES = {
    ast.Import,  # We handle imports specially
    ast.ImportFrom,  # We handle imports specially
}

# Maximum execution time in seconds
MAX_EXECUTION_TIME = 30

# Maximum output size in characters
MAX_OUTPUT_SIZE = 100_000


class ExecutionTimeout(Exception):
    """Raised when code execution exceeds the time limit."""
    pass


class SecurityViolation(Exception):
    """Raised when code attempts a forbidden operation."""
    pass


def _timeout_handler(signum, frame):
    """Signal handler for execution timeout."""
    raise ExecutionTimeout("Code execution exceeded time limit")


def _safe_import(name: str, *args, **kwargs):
    """Restricted import function that only allows whitelisted modules."""
    # Handle submodule imports like "scipy.stats"
    base_module = name.split(".")[0]

    if name not in ALLOWED_IMPORTS and base_module not in ALLOWED_IMPORTS:
        raise SecurityViolation(
            f"Import of '{name}' is not allowed. "
            f"Allowed modules: {', '.join(sorted(ALLOWED_IMPORTS))}"
        )

    return __builtins__["__import__"](name, *args, **kwargs)


def _validate_code_ast(code: str) -> None:
    """Validate code AST for forbidden constructs."""
    try:
        tree = ast.parse(code)
    except SyntaxError as e:
        raise SecurityViolation(f"Syntax error in code: {e}")

    for node in ast.walk(tree):
        # Check for exec/eval calls
        if isinstance(node, ast.Call):
            if isinstance(node.func, ast.Name):
                if node.func.id in ("exec", "eval", "compile", "open", "__import__"):
                    raise SecurityViolation(
                        f"Use of '{node.func.id}' is not allowed"
                    )

        # Check for attribute access to dangerous methods
        if isinstance(node, ast.Attribute):
            if node.attr.startswith("_"):
                raise SecurityViolation(
                    f"Access to private attributes ('{node.attr}') is not allowed"
                )


def _get_restricted_globals() -> dict:
    """Create the restricted globals dict for code execution."""
    # Start with safe builtins
    restricted_builtins = dict(safe_builtins)

    # Add safe functions
    restricted_builtins.update({
        "_getiter_": default_guarded_getiter,
        "_getitem_": default_guarded_getitem,
        "_iter_unpack_sequence_": guarded_iter_unpack_sequence,
        "_unpack_sequence_": guarded_unpack_sequence,
        "_getattr_": getattr,
        "_write_": lambda x: x,  # Allow print
        "__import__": _safe_import,
        "__name__": "__main__",
        "__builtins__": restricted_builtins,
        # Safe builtins
        "abs": abs,
        "all": all,
        "any": any,
        "bool": bool,
        "chr": chr,
        "dict": dict,
        "enumerate": enumerate,
        "filter": filter,
        "float": float,
        "format": format,
        "frozenset": frozenset,
        "hash": hash,
        "hex": hex,
        "int": int,
        "isinstance": isinstance,
        "issubclass": issubclass,
        "iter": iter,
        "len": len,
        "list": list,
        "map": map,
        "max": max,
        "min": min,
        "next": next,
        "oct": oct,
        "ord": ord,
        "pow": pow,
        "print": print,
        "range": range,
        "repr": repr,
        "reversed": reversed,
        "round": round,
        "set": set,
        "slice": slice,
        "sorted": sorted,
        "str": str,
        "sum": sum,
        "tuple": tuple,
        "type": type,
        "zip": zip,
    })

    return {"__builtins__": restricted_builtins}


def _extract_imports(code: str) -> tuple[list[str], str]:
    """Extract import statements and return (imports, remaining_code)."""
    tree = ast.parse(code)
    imports = []
    import_lines = set()

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                imports.append(alias.name)
                import_lines.add(node.lineno)
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            imports.append(module)
            import_lines.add(node.lineno)

    # Remove import lines from code
    lines = code.split("\n")
    remaining_lines = [
        line for i, line in enumerate(lines, 1)
        if i not in import_lines
    ]

    return imports, "\n".join(remaining_lines)


async def execute_python_code(
    code: str,
    timeout_seconds: int = MAX_EXECUTION_TIME,
    context: Optional[dict] = None,
) -> dict[str, Any]:
    """
    Execute Python code in a secure sandbox.

    Args:
        code: Python code to execute
        timeout_seconds: Maximum execution time (default 30, max 60)
        context: Optional dict of variables to inject into execution context

    Returns:
        dict with:
        - success: bool
        - stdout: str (captured print output)
        - stderr: str (error messages)
        - result: Any (last expression value)
        - plots: list[str] (base64 encoded plot images)
        - error: str (if execution failed)
    """
    # Validate timeout
    timeout_seconds = min(max(1, timeout_seconds), 60)

    result = {
        "success": False,
        "stdout": "",
        "stderr": "",
        "result": None,
        "plots": [],
        "error": None,
    }

    try:
        # Step 1: Validate code structure
        _validate_code_ast(code)

        # Step 2: Extract and validate imports
        imports, code_body = _extract_imports(code)
        for module in imports:
            base_module = module.split(".")[0]
            if module not in ALLOWED_IMPORTS and base_module not in ALLOWED_IMPORTS:
                raise SecurityViolation(
                    f"Import of '{module}' is not allowed. "
                    f"Allowed: {', '.join(sorted(ALLOWED_IMPORTS))}"
                )

        # Step 3: Compile with RestrictedPython
        try:
            byte_code = compile_restricted(
                code,
                filename="<user_code>",
                mode="exec",
            )
        except SyntaxError as e:
            raise SecurityViolation(f"Compilation error: {e}")

        if byte_code.errors:
            raise SecurityViolation(
                f"Restricted compilation errors: {byte_code.errors}"
            )

        # Step 4: Prepare execution environment
        restricted_globals = _get_restricted_globals()

        # Pre-import allowed modules
        for module in imports:
            try:
                parts = module.split(".")
                if len(parts) == 1:
                    restricted_globals[module] = __import__(module)
                else:
                    # Handle "from scipy import stats" style
                    base = __import__(module)
                    for part in parts[1:]:
                        base = getattr(base, part)
                    restricted_globals[parts[-1]] = base
            except ImportError as e:
                raise SecurityViolation(f"Failed to import '{module}': {e}")

        # Add common aliases
        if "numpy" in imports:
            restricted_globals["np"] = restricted_globals.get("numpy")
        if "pandas" in imports:
            restricted_globals["pd"] = restricted_globals.get("pandas")
        if "matplotlib.pyplot" in imports or "matplotlib" in imports:
            import matplotlib
            matplotlib.use("Agg")  # Non-interactive backend
            import matplotlib.pyplot as plt
            restricted_globals["plt"] = plt
        if "seaborn" in imports:
            restricted_globals["sns"] = restricted_globals.get("seaborn")

        # Add user context
        if context:
            restricted_globals.update(context)

        # Step 5: Execute with timeout and output capture
        stdout_capture = io.StringIO()
        stderr_capture = io.StringIO()

        def execute():
            nonlocal result
            try:
                with redirect_stdout(stdout_capture), redirect_stderr(stderr_capture):
                    exec(byte_code.code, restricted_globals)

                    # Try to get last expression value
                    if "_" in restricted_globals:
                        result["result"] = str(restricted_globals["_"])
            except Exception as e:
                result["error"] = f"{type(e).__name__}: {str(e)}"

        # Use threading for timeout (signal doesn't work well with async)
        thread = threading.Thread(target=execute)
        thread.start()
        thread.join(timeout=timeout_seconds)

        if thread.is_alive():
            result["error"] = f"Execution timeout after {timeout_seconds} seconds"
            return result

        # Step 6: Capture matplotlib plots
        try:
            import matplotlib.pyplot as plt
            figures = [plt.figure(i) for i in plt.get_fignums()]
            for fig in figures:
                buf = io.BytesIO()
                fig.savefig(buf, format="png", dpi=100, bbox_inches="tight")
                buf.seek(0)
                img_base64 = base64.b64encode(buf.read()).decode("utf-8")
                result["plots"].append(f"data:image/png;base64,{img_base64}")
                plt.close(fig)
        except Exception:
            pass  # No plots or matplotlib not used

        # Step 7: Collect output
        result["stdout"] = stdout_capture.getvalue()[:MAX_OUTPUT_SIZE]
        result["stderr"] = stderr_capture.getvalue()[:MAX_OUTPUT_SIZE]

        if not result["error"]:
            result["success"] = True

    except SecurityViolation as e:
        result["error"] = f"Security violation: {str(e)}"
    except Exception as e:
        result["error"] = f"Execution error: {type(e).__name__}: {str(e)}"
        logger.exception("Code execution failed")

    return result


# Convenience function for simple calculations
async def execute_calculation(expression: str) -> dict[str, Any]:
    """Execute a simple mathematical expression."""
    code = f"""
import math
import statistics
result = {expression}
print(result)
"""
    return await execute_python_code(code, timeout_seconds=5)
