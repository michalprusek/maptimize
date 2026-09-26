"""Guards on the one-off cell-line backfill script.

`scripts/cell_line_backfill.sql` is the only writer that ever runs against
production's 181 existing experiments, and it is hand-written SQL that no
application test path executes. What can be checked statically is checked here.
"""
from pathlib import Path

import pytest

from models.cell_line import DEFAULT_CELL_LINES


def _backfill_sql() -> Path:
    """Locate the one-off script from either layout it is read in.

    In the repo it sits beside `backend/`; under the coverage harness only
    `./backend` is mounted (at `/app`) so `scripts/` is bind-mounted separately.
    Both candidates are checked and a miss is a hard failure, never a skip -- a
    guard that quietly stops running is worse than no guard.
    """
    for candidate in (
        Path(__file__).resolve().parents[3] / "scripts" / "cell_line_backfill.sql",
        Path("/scripts/cell_line_backfill.sql"),
    ):
        if candidate.exists():
            return candidate
    raise FileNotFoundError(
        "cell_line_backfill.sql not found; mount ./scripts into the test "
        "container (see docker-compose.test.yml) so these guards keep running."
    )


@pytest.fixture(scope="module")
def sql() -> str:
    return _backfill_sql().read_text()


@pytest.fixture(scope="module")
def statements(sql) -> str:
    """The script with `--` comments stripped.

    The prose explains what the statements do, in the same words, so counting
    keywords over the raw file counts the explanation too.
    """
    return "\n".join(line.split("--", 1)[0] for line in sql.splitlines())


def test_script_stops_on_the_first_error(sql):
    """Without this psql walks past a failed statement and still exits 0.

    The deploy log then reads as success over a database that was never
    migrated -- the same failure mode this repo already fixed for backups.
    """
    assert "\\set ON_ERROR_STOP on" in sql


def test_the_update_only_fires_while_no_experiment_has_a_line(statements):
    """This is what makes re-running safe, not the `IS NULL` alone.

    `WHERE cell_line_id IS NULL` on its own re-stamps rows a colleague
    deliberately cleared. Gating on the whole column still being empty means the
    second run is a no-op forever after the first, which is the only honest
    reading of "one-off".
    """
    normalised = " ".join(statements.split()).lower()
    assert "not exists (select 1 from experiments where cell_line_id is not null)" in normalised


def test_the_backfill_assigns_the_line_the_model_seeds(sql):
    """The script and `DEFAULT_CELL_LINES` must name the same line.

    They are two writers of one fact: a fresh database gets the row from the
    seed, production got it from here. A rename in one place alone leaves
    production assigning a line that no fresh install has.
    """
    seeded = {entry["name"] for entry in DEFAULT_CELL_LINES}
    assert "U2OS" in seeded
    assert "'U2OS'" in sql


def test_the_script_refuses_to_run_without_the_line_it_assigns(sql, statements):
    """A missing `U2OS` row makes the UPDATE a silent no-op.

    `SET cell_line_id = (SELECT id FROM cell_lines WHERE name = 'U2OS')` yields
    NULL when the row is absent, so the migration would report success having
    changed nothing. The script has to notice that itself.

    ⚠️ Grepping for RAISE EXCEPTION is not enough on its own, and this test said
    so while the guard was dead. The first version of the script INSERTed U2OS
    defensively just above the check, which made the condition unsatisfiable --
    a guard that cannot fire, passing a test that only looked for the word. So
    the real assertion is the second one: nothing in this script may create the
    row it then checks for. `seed_default_data()` is the single writer.
    """
    assert "RAISE EXCEPTION" in sql
    assert "insert into cell_lines" not in " ".join(statements.lower().split())


def test_the_script_touches_no_other_table_or_column(statements):
    """Additive and narrow: one column of one table.

    A backfill that also edited names or deleted rows would be unreviewable from
    its own summary, and this one runs against a colleague's 40+ experiments.
    """
    body = " ".join(statements.lower().split())
    assert "delete from" not in body
    assert "drop " not in body
    # Exactly one UPDATE, and its target is experiments.cell_line_id.
    assert body.count("update ") == 1
    assert "update experiments set cell_line_id" in body
