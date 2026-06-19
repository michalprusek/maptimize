"""Coverage launcher for the isolated test environment (docker-compose.test.yml).

Measuring coverage of this async app fights two native-layer bugs on the
torch 2.11 / coverage 7.x / greenlet stack:

1. ``import torch`` while coverage's tracer is active crashes with
   ``RuntimeError: '_has_torch_function' already has a docstring``.
2. SQLAlchemy-async's asyncpg connection runs inside a greenlet; tracing the app
   *import* and then making that greenlet DB call segfaults.

The only ordering that survives both is: import the whole app FIRST (untraced,
so torch loads cleanly), THEN start coverage, THEN serve. That leaves module-level
lines (imports / ``def`` / decorators) uncounted here, so they are captured by a
separate "import under coverage" Run A (see run-coverage.sh) and combined.

Not used in production - only launched by the test compose.
"""
import atexit
import faulthandler
import os

faulthandler.enable()

# The C tracer is the only core that supports `concurrency = greenlet`.
os.environ["COVERAGE_CORE"] = "ctrace"

# Import the full app BEFORE coverage starts (untraced) — see module docstring.
import main  # noqa: E402

import coverage  # noqa: E402

_cov = coverage.Coverage(config_file=os.environ.get("COVERAGE_RCFILE", "/app/.coveragerc"))
_cov.start()

import uvicorn  # noqa: E402


def _finish() -> None:
    try:
        _cov.stop()
        _cov.save()
    except Exception:
        pass


atexit.register(_finish)

try:
    uvicorn.run(main.app, host="0.0.0.0", port=8000, log_level="warning")
finally:
    _finish()
