#!/usr/bin/env bash
# Measure backend coverage in the isolated test environment (3-way combine).
#   Run B (server): handler bodies, driven by the httpx integration suite.
#   Run C (unit):   services + routers, in-process unit tests (DB/ML/genai mocked).
#   Run A (import): module-level lines (import under coverage).
#   combine -> report (backend/coverage.json + backend/htmlcov).
# Exit status is non-zero if ANY phase fails. Nothing here touches the prod DB.
set -uo pipefail

CF="docker-compose.test.yml"
# Own compose project. Without this, compose derives the project from the repo
# directory ("maptimize") — the same one the prod stack uses — so each stack sees
# the other's containers as orphans and can remove them. Never share with prod.
PROJECT="${COMPOSE_TEST_PROJECT:-maptimize_test}"
DC="docker compose -p $PROJECT -f $CF"
PIP="pip install --quiet --no-input 'coverage[toml]' pytest pytest-asyncio >/dev/null 2>&1"
# Admin = the user init_db seeds automatically; regular = a RESEARCHER we register.
# Password is overridable via env so it need not live in the repo.
ADMIN_EMAIL="${TEST_ADMIN_EMAIL:-12bprusek@gym-nymburk.cz}"
ADMIN_PW="${TEST_ADMIN_PW:-82c17878}"
REG_EMAIL="pytest_regular@utia.cas.cz"
REG_PW="securepass123"
PYTEST_ARGS="${PYTEST_ARGS:--m \"not slow\" -q -p no:cacheprovider}"

echo "== clean old coverage data =="
$DC run --rm --no-deps -T test-backend "rm -f /app/.coverage /app/.coverage.* 2>/dev/null; echo ok" >/dev/null 2>&1

echo "== start server (Run B) =="
$DC up -d --force-recreate test-backend >/dev/null 2>&1
# wait for health
for i in $(seq 1 60); do
  code=$($DC exec -T test-backend sh -c 'curl -s -m 3 -o /dev/null -w "%{http_code}" http://127.0.0.1:8000/health' 2>/dev/null)
  [ "$code" = "200" ] && { echo "   healthy after ~$((i*3))s"; break; }
  # Resolve the container via compose — its name is derived from $PROJECT.
  cid=$($DC ps -q test-backend 2>/dev/null)
  st=$([ -n "$cid" ] && docker inspect "$cid" --format '{{.State.Running}}' 2>/dev/null)
  [ "$st" = "false" ] && { echo "   SERVER DOWN"; $DC logs --tail=20 test-backend; exit 1; }
  sleep 3
done

# Wait for Redis too — a few endpoints (export status, rate limiting) hit it, and
# a cold redis right after `up` makes them 500 instead of their expected status.
for i in $(seq 1 20); do
  [ "$($DC exec -T test-redis redis-cli ping 2>/dev/null | tr -d '\r')" = "PONG" ] && break
  sleep 1
done

echo "== ensure a regular (non-admin) test user exists =="
$DC exec -T test-backend sh -c "curl -s -o /dev/null -w 'register: %{http_code}\n' -X POST http://127.0.0.1:8000/api/auth/register -H 'Content-Type: application/json' -d '{\"name\":\"Pytest Regular\",\"email\":\"$REG_EMAIL\",\"password\":\"$REG_PW\"}'"

echo "== run integration suite (Run B coverage) =="
$DC exec -T \
  -e TEST_API_URL=http://127.0.0.1:8000 \
  -e TEST_USER_EMAIL="$REG_EMAIL" -e TEST_USER_PASSWORD="$REG_PW" \
  -e TEST_ADMIN_EMAIL="$ADMIN_EMAIL" -e TEST_ADMIN_PASSWORD="$ADMIN_PW" \
  test-backend sh -c "cd /app && PYTHONPATH=/app/tests python -m pytest --ignore=/app/tests/unit $PYTEST_ARGS"
TEST_RC=$?
echo "   pytest exit=$TEST_RC"

echo "== run in-process unit suite (Run C coverage) =="
$DC exec -T test-backend sh -c "cd /app && COVERAGE_CORE=ctrace python -m coverage run --rcfile=/app/.coveragerc -m pytest tests/unit -q -p no:cacheprovider"
UNIT_RC=$?
echo "   unit pytest exit=$UNIT_RC"

echo "== stop server to flush coverage =="
$DC stop -t 30 test-backend >/dev/null 2>&1

echo "== Run A (module-level coverage) =="
$DC run --rm --no-deps -T test-backend "$PIP; python /app/tests/_coverage_import.py"
RUNA_RC=$?
[ "$RUNA_RC" -ne 0 ] && echo "   WARNING: Run A (module-level import) failed rc=$RUNA_RC"

echo "== combine + report =="
$DC run --rm --no-deps -T test-backend "$PIP; cd /app && COVERAGE_CORE=ctrace python -m coverage combine && COVERAGE_CORE=ctrace python -m coverage report --precision=1 && COVERAGE_CORE=ctrace python -m coverage json -o /app/coverage.json -q && COVERAGE_CORE=ctrace python -m coverage html -d /app/htmlcov -q"
COMBINE_RC=$?

# Fail the run if ANY phase failed (so CI cannot get a green run from a red suite).
RC=0
for c in "$TEST_RC" "$UNIT_RC" "$RUNA_RC" "$COMBINE_RC"; do [ "$c" -gt "$RC" ] && RC=$c; done
echo "== done (integration=$TEST_RC unit=$UNIT_RC runA=$RUNA_RC combine=$COMBINE_RC) =="
exit $RC
