"""Guards on the one-off cell-line backfill script.

`scripts/cell_line_backfill.sql` is the only writer that ever runs against
production's 181 existing experiments, and it is hand-written SQL that no
application test path executes. What can be checked statically is checked here.
"""
import pytest

from models.cell_line import DEFAULT_CELL_LINES
from tests.unit.conftest import backfill_sql




@pytest.fixture(scope="module")
def sql() -> str:
    return backfill_sql("cell_line_backfill.sql").read_text()


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


def test_the_script_refuses_to_finish_with_a_half_assigned_column(statements):
    """The re-run guard must not become a silent, permanent no-op.

    Deploy order is: restart the backend -- which also makes the group-writable
    chip and the `assign_experiment_cell_line` MCP tool live -- then run this by
    hand. In that window one assignment by anyone (or the agent) makes
    `NOT EXISTS (... IS NOT NULL)` false forever. The UPDATE then matches zero
    rows, nothing errors, psql exits 0, and 180 experiments stay unassigned
    while the summary reads like a success.

    The guard is still right -- backlog and a deliberate clear are
    indistinguishable, so guessing is worse. What is wrong is doing nothing
    QUIETLY. The script has to end by refusing.
    """
    body = " ".join(statements.lower().split())
    # A post-condition on the resulting column, not just the pre-condition on
    # the row it assigns from.
    assert body.count("raise exception") == 2, (
        "expected two RAISEs: the missing seed row, and a column left half "
        "assigned after the UPDATE declined to run"
    )
    assert "cell_line_id is null" in body


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
