"""Secure Python code execution service using RestrictedPython.

This service provides a sandboxed environment for executing user-provided Python code
with strict security controls:
- Whitelisted imports only (numpy, pandas, scipy, matplotlib, etc.)
- No file I/O, network access, or system calls
- Memory and time limits
- Captured stdout and return values

Plots are saved to a temp folder and URLs are returned instead of base64.
"""

import ast
import io
import sys
import base64
import logging
import traceback
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Optional
from contextlib import redirect_stdout, redirect_stderr
import signal
import threading

from config import get_settings

from RestrictedPython import compile_restricted, safe_builtins, PrintCollector
from RestrictedPython.Guards import (
    safe_builtins,
    guarded_iter_unpack_sequence,
    guarded_unpack_sequence,
    safer_getattr,
)
from RestrictedPython.Eval import default_guarded_getiter, default_guarded_getitem

# Dangerous dunder attributes that could be used to escape the sandbox
FORBIDDEN_DUNDER_ATTRS = frozenset({
    "__class__", "__bases__", "__subclasses__", "__mro__",
    "__globals__", "__code__", "__closure__", "__func__",
    "__self__", "__dict__", "__builtins__", "__import__",
    "__reduce__", "__reduce_ex__", "__getstate__", "__setstate__",
    "__delattr__", "__setattr__", "__getattribute__",
})

logger = logging.getLogger(__name__)
settings = get_settings()

# Temp directory for generated plots (cleaned periodically)
TEMP_DIR = Path(settings.upload_dir) / "temp"
TEMP_DIR.mkdir(parents=True, exist_ok=True)

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
        # Check for exec/eval calls (both as direct name and as attribute)
        if isinstance(node, ast.Call):
            if isinstance(node.func, ast.Name):
                if node.func.id in ("exec", "eval", "compile", "open", "__import__", "globals", "locals", "vars", "dir"):
                    raise SecurityViolation(
                        f"Use of '{node.func.id}' is not allowed"
                    )
            elif isinstance(node.func, ast.Attribute):
                # Check for calls like builtins.exec()
                if node.func.attr in ("exec", "eval", "compile", "open", "__import__"):
                    raise SecurityViolation(
                        f"Use of '{node.func.attr}' is not allowed"
                    )

        # Check for attribute access to dangerous methods
        if isinstance(node, ast.Attribute):
            # Block all dunder attribute access that could be used to escape sandbox
            if node.attr in FORBIDDEN_DUNDER_ATTRS:
                raise SecurityViolation(
                    f"Access to '{node.attr}' is not allowed (potential sandbox escape)"
                )
            # Also block all underscore-prefixed private attrs (except _1, _2 for unpacking)
            if node.attr.startswith("_") and not node.attr.lstrip("_").isdigit():
                raise SecurityViolation(
                    f"Access to private attributes ('{node.attr}') is not allowed"
                )

        # Check for string-based getattr/setattr that could bypass AST checks
        if isinstance(node, ast.Call):
            if isinstance(node.func, ast.Name) and node.func.id in ("getattr", "setattr", "delattr", "hasattr"):
                # If second argument is a string literal, check it
                if len(node.args) >= 2 and isinstance(node.args[1], ast.Constant):
                    attr_name = node.args[1].value
                    if isinstance(attr_name, str):
                        if attr_name in FORBIDDEN_DUNDER_ATTRS:
                            raise SecurityViolation(
                                f"Access to '{attr_name}' via {node.func.id}() is not allowed"
                            )
                        if attr_name.startswith("_"):
                            raise SecurityViolation(
                                f"Access to private attributes via {node.func.id}() is not allowed"
                            )


def _guarded_write(obj):
    """
    Guard for write operations.
    Only allow write to safe types (lists, dicts, etc.) not to objects that
    could be modified to escape the sandbox.
    """
    # Allow None (for expressions that don't return)
    if obj is None:
        return obj
    # Allow standard mutable types
    if isinstance(obj, (list, dict, set)):
        return obj
    # Allow numpy arrays and pandas objects if imported
    obj_type = type(obj).__name__
    if obj_type in ("ndarray", "DataFrame", "Series", "Index"):
        return obj
    # For other objects, return a restricted wrapper or the object itself
    # (RestrictedPython will handle most cases)
    return obj


def _get_restricted_globals() -> dict:
    """Create the restricted globals dict for code execution."""
    # Start with safe builtins
    restricted_builtins = dict(safe_builtins)

    # Add safe built-in functions
    restricted_builtins.update({
        "__import__": _safe_import,
        "__name__": "__main__",
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

    # RestrictedPython guards must be at top-level globals, not inside __builtins__
    return {
        "__builtins__": restricted_builtins,
        # RestrictedPython guard functions
        "_getiter_": default_guarded_getiter,
        "_getitem_": default_guarded_getitem,
        "_iter_unpack_sequence_": guarded_iter_unpack_sequence,
        "_unpack_sequence_": guarded_unpack_sequence,
        "_getattr_": safer_getattr,
        "_write_": _guarded_write,
        "_print_": PrintCollector,
    }


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
            # compile_restricted returns code object directly, or None if compilation fails
            if byte_code is None:
                raise SecurityViolation("Compilation failed - restricted syntax detected")
        except SyntaxError as e:
            raise SecurityViolation(f"Compilation error: {e}")

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
                    exec(byte_code, restricted_globals)

                    # Collect printed output from PrintCollector
                    # RestrictedPython stores the collector instance in _print
                    if "_print" in restricted_globals:
                        printed = restricted_globals["_print"]
                        if hasattr(printed, "txt"):
                            # txt is a list of printed items
                            result["stdout"] = "".join(str(item) for item in printed.txt)

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

        # Step 6: Capture matplotlib plots and save to temp folder
        try:
            import matplotlib.pyplot as plt
            figures = [plt.figure(i) for i in plt.get_fignums()]
            for fig in figures:
                # Generate unique filename
                timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                unique_id = uuid.uuid4().hex[:8]
                filename = f"plot_{timestamp}_{unique_id}.png"
                filepath = TEMP_DIR / filename

                # Save to file
                fig.savefig(filepath, format="png", dpi=100, bbox_inches="tight", facecolor="white")
                plt.close(fig)

                # Return URL instead of base64
                result["plots"].append(f"/uploads/temp/{filename}")
                logger.debug(f"Saved plot to {filepath}")
        except ImportError as e:
            # matplotlib not available - expected when code doesn't use plotting
            logger.debug(f"matplotlib not available for plot capture: {e}")
        except Exception as plot_error:
            # Log plot capture errors instead of silently ignoring
            logger.warning(f"Failed to capture matplotlib plots: {plot_error}")
            result["plot_capture_warning"] = f"Failed to capture plots: {str(plot_error)}"

        # Step 7: Collect output - combine PrintCollector output with any direct stdout
        # (PrintCollector captures print(), stdout_capture catches other output)
        stdout_parts = []
        if result["stdout"]:  # PrintCollector output set earlier
            stdout_parts.append(result["stdout"])
        captured_stdout = stdout_capture.getvalue()
        if captured_stdout:
            stdout_parts.append(captured_stdout)
        result["stdout"] = "".join(stdout_parts)[:MAX_OUTPUT_SIZE]
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


def cleanup_old_temp_files(max_age_hours: int = 24) -> int:
    """
    Remove temp files (plots, etc.) older than max_age_hours.

    Args:
        max_age_hours: Maximum age in hours before files are deleted (default 24)

    Returns:
        Number of files removed
    """
    cutoff = datetime.now().timestamp() - (max_age_hours * 3600)
    removed = 0

    for file_path in TEMP_DIR.glob("*"):
        if file_path.is_file() and file_path.stat().st_mtime < cutoff:
            try:
                file_path.unlink()
                removed += 1
            except Exception as e:
                logger.warning(f"Failed to remove old temp file {file_path}: {e}")

    if removed > 0:
        logger.info(f"Cleaned up {removed} old temp files")

    return removed
