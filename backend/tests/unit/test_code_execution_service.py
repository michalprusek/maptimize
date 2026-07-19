"""Unit tests for services.code_execution_service.

The service runs user code in a RestrictedPython sandbox inside a separate
``multiprocessing.Process``. Real subprocesses do not work under coverage (and
would not be counted), so these tests patch the module's ``multiprocessing``
reference with a fake that runs the target *synchronously* in-process. This
both exercises the subprocess body (``execute_in_process``) for coverage and
keeps the test deterministic.
"""
import resource
from pathlib import Path
from unittest.mock import patch

import pytest

import services.code_execution_service as ces
from services.code_execution_service import (
    ExecutionTimeout,
    SecurityViolation,
    _extract_imports,
    _guarded_write,
    _safe_import,
    _set_resource_limits,
    _timeout_handler,
    _validate_code_ast,
    _get_restricted_globals,
    cleanup_old_temp_files,
    execute_calculation,
    execute_python_code,
)


@pytest.fixture(autouse=True)
def _no_real_rlimits():
    """Never apply real OS resource limits to the pytest process.

    Sandbox code runs in-process here (multiprocessing is faked for coverage), so a
    real setrlimit(RLIMIT_CPU) would constrain the test runner itself and SIGXCPU it
    after enough accumulated CPU time. Mock setrlimit so the calls still execute and
    are counted, without limiting the process.
    """
    with patch.object(resource, "setrlimit"):
        yield


# --------------------------------------------------------------------------- #
# Fake multiprocessing primitives that run the target synchronously in-process
# --------------------------------------------------------------------------- #
class _SyncQueue:
    """Drop-in for multiprocessing.Queue running in the same process."""

    def __init__(self):
        self._items = []

    def put(self, item):
        self._items.append(item)

    def get_nowait(self):
        if not self._items:
            raise Exception("empty queue")
        return self._items.pop(0)


class _FailingQueue(_SyncQueue):
    """Queue whose get_nowait always raises (retrieval-failure branch)."""

    def get_nowait(self):
        raise Exception("boom")


class _SyncProcess:
    """Runs target(*args) on start(); never 'alive' after join."""

    def __init__(self, target, args):
        self._target = target
        self._args = args
        self._ran = False

    def start(self):
        # Execute the subprocess body synchronously so coverage sees it.
        self._target(*self._args)
        self._ran = True

    def join(self, timeout=None):
        return None

    def is_alive(self):
        return False

    def terminate(self):  # pragma: no cover - only used by timeout fake
        pass

    def kill(self):  # pragma: no cover
        pass


class _TimeoutProcess(_SyncProcess):
    """Pretends to run forever so the timeout branch triggers."""

    def __init__(self, target, args):
        super().__init__(target, args)
        self._alive = True

    def start(self):
        # Do NOT run the target (simulating a hung process).
        pass

    def is_alive(self):
        # First call -> True (triggers terminate), later calls -> False.
        alive = self._alive
        self._alive = False
        return alive


class _StuckProcess(_TimeoutProcess):
    """Stays alive even after terminate(), forcing the kill() path."""

    def __init__(self, target, args):
        super().__init__(target, args)
        self._calls = 0

    def is_alive(self):
        # Alive for the first two checks (after start, after terminate),
        # then dead so the final join can complete.
        self._calls += 1
        return self._calls <= 2


def _fake_mp(process_cls=_SyncProcess, queue_cls=_SyncQueue):
    """Build a fake `multiprocessing` module substitute."""
    class _MP:
        Process = staticmethod(lambda target, args: process_cls(target, args))
        Queue = staticmethod(queue_cls)
    return _MP


async def _run(code, **kwargs):
    """Run execute_python_code with the synchronous multiprocessing fake.

    The patch MUST stay active while the coroutine is awaited, otherwise the
    real ``multiprocessing`` is restored before the code actually executes and a
    real subprocess is spawned (which coverage cannot trace).
    """
    with patch.object(ces, "multiprocessing", _fake_mp()):
        return await execute_python_code(code, **kwargs)


# --------------------------------------------------------------------------- #
# _validate_code_ast
# --------------------------------------------------------------------------- #
def test_validate_ok():
    _validate_code_ast("x = 1 + 2\nprint(x)")  # no exception


def test_validate_syntax_error():
    with pytest.raises(SecurityViolation, match="Syntax error"):
        _validate_code_ast("def (:")


@pytest.mark.parametrize("name", ["exec", "eval", "compile", "open",
                                  "__import__", "globals", "locals", "vars", "dir"])
def test_validate_forbidden_name_calls(name):
    with pytest.raises(SecurityViolation, match="not allowed"):
        _validate_code_ast(f"{name}('x')")


def test_validate_forbidden_attribute_call():
    with pytest.raises(SecurityViolation, match="not allowed"):
        _validate_code_ast("builtins.exec('x')")


def test_validate_forbidden_dunder_attr():
    with pytest.raises(SecurityViolation, match="sandbox escape"):
        _validate_code_ast("x.__class__")


def test_validate_private_attr_blocked():
    with pytest.raises(SecurityViolation, match="private attributes"):
        _validate_code_ast("x._secret")


def test_validate_numeric_unpack_attr_allowed():
    # _1 / _2 style attrs are tolerated (used by unpacking) -> no exception.
    _validate_code_ast("x._1")


def test_validate_getattr_dunder_literal():
    with pytest.raises(SecurityViolation, match="is not allowed"):
        _validate_code_ast("getattr(x, '__class__')")


def test_validate_getattr_private_literal():
    with pytest.raises(SecurityViolation, match="private attributes"):
        _validate_code_ast("setattr(x, '_foo', 1)")


def test_validate_getattr_nonstring_arg_ok():
    # Second arg not a string constant -> the literal check is skipped.
    _validate_code_ast("getattr(x, y)")


def test_validate_getattr_safe_attr_ok():
    _validate_code_ast("getattr(x, 'value')")


# --------------------------------------------------------------------------- #
# _safe_import
# --------------------------------------------------------------------------- #
def test_safe_import_allowed():
    mod = _safe_import("math")
    assert mod.sqrt(4) == 2


def test_safe_import_submodule_allowed():
    # Empty fromlist mirrors `import a.b` / `import a.b as c`: __import__
    # returns the TOP-LEVEL package and the interpreter walks the chain.
    mod = _safe_import("matplotlib.pyplot")
    assert mod.__name__ == "matplotlib"
    assert hasattr(mod.pyplot, "figure")


def test_safe_import_submodule_with_fromlist_returns_submodule():
    # `from a.b import c` passes a fromlist and must get the submodule.
    mod = _safe_import("matplotlib.pyplot", fromlist=("figure",))
    assert mod.__name__ == "matplotlib.pyplot"
    assert hasattr(mod, "figure")


def test_safe_import_forbidden():
    with pytest.raises(SecurityViolation, match="not allowed"):
        _safe_import("os")


# --------------------------------------------------------------------------- #
# _extract_imports
# --------------------------------------------------------------------------- #
def test_extract_imports_plain():
    imports, body = _extract_imports("import numpy\nx = 1")
    assert imports == ["numpy"]
    assert "import numpy" not in body
    assert "x = 1" in body


def test_extract_imports_from():
    imports, body = _extract_imports("from scipy import stats\ny = 2")
    assert imports == ["scipy"]
    assert "from scipy" not in body


def test_extract_imports_from_no_module():
    # `from . import x` -> module is None -> coerced to "".
    imports, _ = _extract_imports("x = 1")
    assert imports == []


# --------------------------------------------------------------------------- #
# _guarded_write
# --------------------------------------------------------------------------- #
def test_guarded_write_none():
    assert _guarded_write(None) is None


@pytest.mark.parametrize("val", [[1], {"a": 1}, {1, 2}])
def test_guarded_write_mutable(val):
    assert _guarded_write(val) is val


def test_guarded_write_numpy():
    import numpy as np
    arr = np.array([1, 2, 3])
    assert _guarded_write(arr) is arr


def test_guarded_write_other_object():
    obj = object()
    assert _guarded_write(obj) is obj


# --------------------------------------------------------------------------- #
# _get_restricted_globals
# --------------------------------------------------------------------------- #
def test_restricted_globals_structure():
    g = _get_restricted_globals()
    assert "__builtins__" in g
    assert g["_write_"] is _guarded_write
    assert g["__builtins__"]["__import__"] is _safe_import
    assert g["__builtins__"]["len"] is len


# --------------------------------------------------------------------------- #
# _set_resource_limits / _timeout_handler
# --------------------------------------------------------------------------- #
def test_set_resource_limits_runs():
    # Re-patch locally so we can assert all four limits are set (the autouse
    # fixture already prevents real limits from touching the test process).
    with patch.object(resource, "setrlimit") as srl:
        _set_resource_limits()
    assert srl.call_count == 4


def test_set_resource_limits_handles_failures():
    def boom(*a, **k):
        raise ValueError("nope")
    with patch.object(resource, "setrlimit", side_effect=boom):
        _set_resource_limits()  # all four branches log/swallow


def test_timeout_handler_raises():
    with pytest.raises(ExecutionTimeout):
        _timeout_handler(None, None)


# --------------------------------------------------------------------------- #
# execute_python_code - happy paths
# --------------------------------------------------------------------------- #
async def test_execute_simple_print():
    res = await _run("print('hello world')")
    assert res["success"] is True
    assert "hello world" in res["stdout"]
    assert res["error"] is None


async def test_execute_with_numpy():
    res = await _run("import numpy as np\nprint(np.sum(np.array([1, 2, 3])))")
    assert res["success"] is True
    assert "6" in res["stdout"]


async def test_execute_last_expression_result():
    # The service reports the value of a global named `_` as `result`.
    res = await _run("_ = 41 + 1")
    assert res["success"] is True
    assert res["result"] == "42"


async def test_execute_with_context():
    res = await _run("print(injected)", context={"injected": 99})
    assert res["success"] is True
    assert "99" in res["stdout"]


async def test_execute_context_skips_complex_types():
    # Non-serializable context values are dropped before reaching the subprocess.
    class Weird:
        pass
    res = await _run("print('ok')", context={"bad": Weird(), "good": 5})
    assert res["success"] is True
    assert "ok" in res["stdout"]


async def test_execute_pandas_alias():
    res = await _run("import pandas as pd\nprint(pd.Series([1, 2]).sum())")
    assert res["success"] is True
    assert "3" in res["stdout"]


# --------------------------------------------------------------------------- #
# execute_python_code - matplotlib plot capture
# --------------------------------------------------------------------------- #
async def test_execute_matplotlib_saves_plot(tmp_path):
    # `plt` is injected as a global alias by the service, so the user code uses
    # it directly (avoids RestrictedPython's `import ... as` aliasing path).
    code = (
        "import matplotlib\n"
        "plt.figure()\n"
        "plt.plot([1, 2, 3], [4, 5, 6])\n"
    )
    with patch.object(ces, "CHAT_IMAGE_DIR", tmp_path):
        res = await _run(code, user_id=7)
    assert res["success"] is True
    assert len(res["plots"]) == 1
    # Persistent (not the 24h-reaped temp dir) and served per user through an
    # authenticated endpoint rather than the public /uploads mount.
    assert res["plots"][0].startswith("/api/chat-images/7/plot_")
    assert res["plots"][0].endswith(".webp")
    saved = list((Path(tmp_path) / "7").glob("plot_*.webp"))
    assert len(saved) == 1


async def test_execute_matplotlib_pyplot_submodule(tmp_path):
    # Importing the matplotlib.pyplot submodule exercises the pyplot alias branch.
    code = (
        "import matplotlib.pyplot\n"
        "plt.figure()\n"
        "plt.plot([0, 1])\n"
    )
    with patch.object(ces, "CHAT_IMAGE_DIR", tmp_path):
        res = await _run(code)
    assert res["success"] is True
    assert len(res["plots"]) >= 1


async def test_execute_aliased_submodule_import(tmp_path):
    # `import a.b as c` -- __import__ must return the top-level package for the
    # interpreter to walk. Returning the submodule broke the single most common
    # line the agent writes: `import matplotlib.pyplot as plt`.
    code = (
        "import matplotlib.pyplot as plt\n"
        "plt.figure()\n"
        "plt.plot([1, 2, 3], [2, 4, 8])\n"
    )
    with patch.object(ces, "CHAT_IMAGE_DIR", tmp_path):
        res = await _run(code, user_id=7)
    assert res["success"] is True, res.get("error")
    assert len(res["plots"]) == 1


async def test_execute_aliased_submodule_import_nonplot():
    # Same aliasing path for a non-matplotlib module (no pre-injected global).
    res = await _run("import scipy.stats as st\nprint(round(st.norm.cdf(1), 3))")
    assert res["success"] is True, res.get("error")
    assert "0.841" in res["stdout"]


async def test_execute_seaborn_alias():
    # `sns` is injected as a global alias by the service. seaborn's real import
    # chain is unstable under the mocked-native-lib test env, so inject a fake
    # `seaborn` into sys.modules and exercise only the alias branches.
    import sys
    import types

    fake_seaborn = types.ModuleType("seaborn")
    fake_seaborn.set_theme = lambda *a, **k: None
    with patch.dict(sys.modules, {"seaborn": fake_seaborn}):
        code = "import seaborn\nprint(sns is not None)"
        res = await _run(code)
    assert res["success"] is True
    assert "True" in res["stdout"]


# --------------------------------------------------------------------------- #
# execute_python_code - error / rejection branches
# --------------------------------------------------------------------------- #
async def test_execute_security_violation_dunder():
    res = await _run("x = (1).__class__")
    assert res["success"] is False
    assert "Security violation" in res["error"]


async def test_execute_forbidden_import_rejected():
    res = await _run("import os\nprint(os.getcwd())")
    assert res["success"] is False
    assert "Security violation" in res["error"]
    assert "os" in res["error"]


async def test_execute_syntax_error():
    res = await _run("def (:")
    assert res["success"] is False
    assert "Security violation" in res["error"]


async def test_execute_runtime_error_in_user_code():
    res = await _run("x = 1 / 0")
    assert res["success"] is False
    assert "ZeroDivisionError" in res["error"]


async def test_execute_failed_import_inside_subprocess():
    # `statistics` is whitelisted at the top-level AST/import gate, but make the
    # subprocess importlib.import_module fail to hit the ImportError branch.
    import importlib
    real = importlib.import_module

    def fake_import(name, *a, **k):
        if name == "statistics":
            raise ImportError("simulated missing module")
        return real(name, *a, **k)

    with patch("importlib.import_module", side_effect=fake_import):
        res = await _run("import statistics\nprint(statistics.mean([1, 2]))")
    assert res["success"] is False
    assert "Failed to import 'statistics'" in res["error"]


async def test_execute_main_compile_returns_none():
    # compile_restricted returning None in the main process -> SecurityViolation.
    with patch.object(ces, "compile_restricted", return_value=None):
        res = await _run("print('x')")
    assert res["success"] is False
    assert "Security violation" in res["error"]


async def test_execute_main_compile_syntax_error():
    # compile_restricted raising SyntaxError in the main process.
    with patch.object(ces, "compile_restricted", side_effect=SyntaxError("bad")):
        res = await _run("print('x')")
    assert res["success"] is False
    assert "Security violation" in res["error"]


async def test_execute_subprocess_compile_returns_none():
    # Main compile succeeds, subprocess compile returns None -> subprocess error.
    real_compile = ces.compile_restricted
    calls = {"n": 0}

    def fake_compile(*a, **k):
        calls["n"] += 1
        if calls["n"] == 1:
            return real_compile(*a, **k)  # main process compiles fine
        return None  # subprocess compile fails

    with patch.object(ces, "compile_restricted", side_effect=fake_compile):
        res = await _run("print('x')")
    assert res["success"] is False
    assert "Compilation failed in subprocess" in res["error"]


async def test_execute_main_import_error():
    # Main-process pre-import of an allowed module fails -> SecurityViolation.
    import builtins
    real_import = builtins.__import__

    def fake_import(name, *a, **k):
        if name == "math":
            raise ImportError("simulated")
        return real_import(name, *a, **k)

    with patch("builtins.__import__", side_effect=fake_import):
        res = await _run("import math\nprint(math.pi)")
    assert res["success"] is False
    assert "Failed to import 'math'" in res["error"]


async def test_execute_subprocess_memory_error():
    # User code that raises MemoryError hits the subprocess MemoryError handler.
    res = await _run("raise MemoryError()")
    assert res["success"] is False
    assert "Memory limit exceeded" in res["error"]


async def test_execute_subprocess_recursion_error():
    # Genuine unbounded recursion trips Python's recursion limit, hitting the
    # subprocess RecursionError handler.
    res = await _run("def f(n):\n    return f(n + 1)\nf(0)\n")
    assert res["success"] is False
    assert "recursion depth" in res["error"].lower()


async def test_execute_plot_capture_import_error(tmp_path):
    # The plot-capture block does `import matplotlib.pyplot as plt`; if that
    # import fails, the ImportError branch is swallowed and execution succeeds.
    # User code imports only numpy (no matplotlib alias), so blocking the pyplot
    # import affects only the capture step.
    import builtins
    real_import = builtins.__import__

    def block_pyplot(name, *a, **k):
        if name == "matplotlib.pyplot":
            raise ImportError("no pyplot")
        return real_import(name, *a, **k)

    with patch.object(ces, "TEMP_DIR", tmp_path):
        with patch("builtins.__import__", side_effect=block_pyplot):
            res = await _run("import numpy as np\nprint(np.sum([1]))")
    assert res["success"] is True
    assert res["plots"] == []


async def test_execute_plot_capture_generic_error(tmp_path):
    # An error during fig.savefig is caught as plot_capture_warning, success stays.
    code = "import matplotlib\nplt.figure()\nplt.plot([1, 2])\n"
    with patch.object(ces, "TEMP_DIR", tmp_path):
        with patch("matplotlib.figure.Figure.savefig",
                   side_effect=RuntimeError("disk full")):
            res = await _run(code)
    assert res["success"] is True
    assert res["plots"] == []


# --------------------------------------------------------------------------- #
# execute_python_code - timeout / process-control branches
# --------------------------------------------------------------------------- #
async def test_execute_timeout_terminates_process():
    with patch.object(ces, "multiprocessing", _fake_mp(process_cls=_TimeoutProcess)):
        res = await execute_python_code("print('x')", timeout_seconds=1)
    assert res["success"] is False
    assert "timeout" in res["error"].lower()


async def test_execute_timeout_force_kill():
    with patch.object(ces, "multiprocessing", _fake_mp(process_cls=_StuckProcess)):
        res = await execute_python_code("print('x')", timeout_seconds=1)
    assert res["success"] is False
    assert "timeout" in res["error"].lower()


async def test_execute_result_retrieval_failure():
    with patch.object(ces, "multiprocessing",
                      _fake_mp(process_cls=_SyncProcess, queue_cls=_FailingQueue)):
        res = await execute_python_code("print('x')")
    assert res["success"] is False
    assert "Failed to retrieve execution result" in res["error"]


async def test_execute_timeout_clamped():
    # timeout_seconds above the max (60) is clamped; just confirm it still runs.
    res = await _run("print('clamp')", timeout_seconds=999)
    assert res["success"] is True


async def test_execute_timeout_min_clamped():
    res = await _run("print('clampmin')", timeout_seconds=0)
    assert res["success"] is True


# --------------------------------------------------------------------------- #
# execute_python_code - outer exception handling
# --------------------------------------------------------------------------- #
async def test_execute_outer_unexpected_exception():
    # Make AST validation blow up with a non-SecurityViolation error to hit the
    # generic `except Exception` branch in execute_python_code.
    with patch.object(ces, "_validate_code_ast", side_effect=RuntimeError("kaboom")):
        res = await _run("print('x')")
    assert res["success"] is False
    assert "Execution error" in res["error"]
    assert "RuntimeError" in res["error"]


async def test_execute_outer_import_error():
    with patch.object(ces, "_validate_code_ast", side_effect=ImportError("nomod")):
        res = await _run("print('x')")
    assert res["success"] is False
    assert "Import failed" in res["error"]


async def test_execute_outer_memory_error():
    with patch.object(ces, "_validate_code_ast", side_effect=MemoryError()):
        res = await _run("print('x')")
    assert res["success"] is False
    assert "Memory limit exceeded" in res["error"]


async def test_execute_outer_recursion_error():
    with patch.object(ces, "_validate_code_ast", side_effect=RecursionError()):
        res = await _run("print('x')")
    assert res["success"] is False
    assert "recursion depth" in res["error"].lower()


# --------------------------------------------------------------------------- #
# execute_calculation
# --------------------------------------------------------------------------- #
async def test_execute_calculation():
    with patch.object(ces, "multiprocessing", _fake_mp()):
        res = await execute_calculation("2 + 2 * 3")
    assert res["success"] is True
    assert "8" in res["stdout"]


async def test_execute_calculation_with_math():
    with patch.object(ces, "multiprocessing", _fake_mp()):
        res = await execute_calculation("math.sqrt(16)")
    assert res["success"] is True
    assert "4.0" in res["stdout"]


# --------------------------------------------------------------------------- #
# cleanup_old_temp_files
# --------------------------------------------------------------------------- #
def test_cleanup_old_temp_files(tmp_path):
    old = tmp_path / "plot_old.png"
    new = tmp_path / "plot_new.png"
    old.write_text("x")
    new.write_text("y")
    # Backdate the "old" file beyond the cutoff.
    import os
    old_time = old.stat().st_mtime - (48 * 3600)
    os.utime(old, (old_time, old_time))

    with patch.object(ces, "TEMP_DIR", tmp_path):
        removed = cleanup_old_temp_files(max_age_hours=24)
    assert removed == 1
    assert not old.exists()
    assert new.exists()


def test_cleanup_old_temp_files_missing_dir(tmp_path):
    missing = tmp_path / "does_not_exist"
    with patch.object(ces, "TEMP_DIR", missing):
        assert cleanup_old_temp_files() == 0
