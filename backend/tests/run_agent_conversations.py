"""Live conversation smoke-test for the Gemini chat agent.

Not a pytest: it calls the real Gemini API and the real (prod/dev) database and
GPU, so it costs money and cannot run offline. Run it by hand after changing the
agent loop, tools, prompt, or model:

    docker exec maptimize-backend python /app/tests/run_agent_conversations.py

It sends a spread of questions -- simple, DB-heavy (the kind that used to make
the agent thrash), plotting, and web -- through ``generate_response`` exactly as
the router does (a fresh DB session per turn) and flags:

  FAIL      the reply is empty or is one of the fallback/error messages
  NEAR-CAP  the turn used >= FORCE_ANSWER_AFTER tool calls (imported from the
            agent loop; it nearly ran away but still forced a synthesis)
  OK        a normal answer within budget

Exit code is non-zero if any turn FAILed, so it can gate a deploy.
"""
import argparse
import asyncio
import sys
import time

sys.path.insert(0, "/app")

from database import get_db_context  # noqa: E402
from services.gemini_agent_service import (  # noqa: E402
    FORCE_ANSWER_AFTER,
    generate_response,
)
from sqlalchemy import text  # noqa: E402

# Tool count at/above which the loop forces an answer -- imported from the loop
# (single source of truth) so this never drifts. Treat >= this as "nearly ran
# away": the loop still forces a synthesis, but the question over-explored.
NEAR_CAP = FORCE_ANSWER_AFTER

QUESTIONS = [
    ("Kolik mám experimentů a kolik buněk celkem?"),
    ("Vypiš experimenty seřazené podle počtu buněk sestupně."),
    ("Jaké proteiny mám v databázi a jaká mají UniProt ID?"),
    ("Which experiment has the highest average cell mean_intensity?"),
    ("Porovnej průměrný bundleness_score mezi proteiny."),
    ("Udělej histogram rozdělení mean_intensity všech buněk."),
    ("What is the PRC1 protein? Use the web if needed."),
]

# Substrings that mean the agent gave up rather than answering.
FAIL_MARKERS = [
    "please try your query again",
    "couldn't compose a final answer",
    "an error occurred while processing",
    "wasn't able to generate",
    "took too long to respond",
    "ai service is not configured",
    "completed actions:",  # the old tool-list dump must never come back
]


async def run(questions, per_turn_timeout):
    async with get_db_context() as db:
        uid = (await db.execute(text("SELECT id FROM users ORDER BY id LIMIT 1"))).scalar()
    if uid is None:
        print("No users in the database; cannot run.")
        return 1

    print(f"user={uid}  questions={len(questions)}")
    print("=" * 80)
    failures = 0
    max_tools = 0
    for i, q in enumerate(questions):
        async with get_db_context() as db:
            t0 = time.time()
            try:
                r = await asyncio.wait_for(
                    generate_response(q, uid, 990000 + i, db), timeout=per_turn_timeout)
            except Exception as e:
                print(f"[EXC     ] {q[:50]}\n           {type(e).__name__}: {e}")
                failures += 1
                continue
            dt = time.time() - t0

        content = (r.get("content") or "").strip()
        calls = r.get("tool_calls") or []
        n = len(calls)
        max_tools = max(max_tools, n)
        used = ",".join(dict.fromkeys(tc["tool"] for tc in calls))
        low = content.lower()
        if not content or any(m in low for m in FAIL_MARKERS):
            verdict = "FAIL"
            failures += 1
        elif n >= NEAR_CAP:
            verdict = "NEAR-CAP"
        else:
            verdict = "OK"

        print(f"[{verdict:8}] tools={n:2}  {dt:5.1f}s  {q[:52]}")
        print(f"           used: {used[:100]}")
        print(f"           ans:  {content.replace(chr(10), ' ')[:110]}")

    print("=" * 80)
    print(f"FAIL={failures}  max_tools_in_a_turn={max_tools}")
    return 1 if failures else 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--timeout", type=int, default=240, help="per-turn timeout (s)")
    ap.add_argument("-q", "--question", action="append", help="override question(s)")
    args = ap.parse_args()
    questions = args.question or QUESTIONS
    sys.exit(asyncio.run(run(questions, args.timeout)))


if __name__ == "__main__":
    main()
