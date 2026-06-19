"""Run A: capture module-level coverage (imports / def / decorators / constants).

Imports the whole app UNDER coverage so module-level lines count. Heavy libs are
pre-imported first (torch docstring bug) and no DB connection is made (so the
asyncpg-greenlet segfault can't fire). Combined with the server's body coverage.
"""
import faulthandler
import importlib
import os
import pkgutil

faulthandler.enable()
os.environ["COVERAGE_CORE"] = "ctrace"

for _lib in ("torch", "torchvision", "cv2"):
    try:
        importlib.import_module(_lib)
    except Exception:
        pass

import coverage  # noqa: E402

_cov = coverage.Coverage(config_file=os.environ.get("COVERAGE_RCFILE", "/app/.coveragerc"))
_cov.start()

# Import the app entrypoint, then walk every source package so module-level code
# in lazily-referenced modules is also recorded.
try:
    import main  # noqa: F401
except Exception as exc:  # pragma: no cover
    print("MAIN_IMPORT_FAIL", type(exc).__name__, exc)

for _pkg in ("routers", "services", "models", "schemas", "utils"):
    try:
        _p = importlib.import_module(_pkg)
    except Exception:
        continue
    for _m in pkgutil.walk_packages(_p.__path__, _p.__name__ + "."):
        try:
            importlib.import_module(_m.name)
        except Exception:
            pass

_cov.stop()
_cov.save()
print("RUN_A_DONE")
