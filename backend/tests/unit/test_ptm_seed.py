"""The seeded PTM vocabulary, and the two entries that are not modifications.

`seed_default_data()` fires only on an empty table, so these values are what a
fresh install and the test database start from. The marker channel on the
projections is driven entirely by `kind`, so a seed that shipped every row as
`modification` would draw every point identically with nothing failing anywhere
— which is precisely the failure this file exists to make loud.
"""
from models.ptm import DEFAULT_PTMS, PTM, PTM_KIND_CHECK, PTMKind, _kind_check_sql
from tests.unit.conftest import backfill_sql



def _named(kind: PTMKind) -> list[str]:
    return [p["name"] for p in DEFAULT_PTMS if p.get("kind") == kind.value]


def test_the_seed_offers_a_control_named_for_the_condition_it_pairs_with():
    # The control plasmid differs between PTM conditions, so a row called plain
    # "Control" pools samples that are not comparable. The seed ships the one
    # pairing the lab actually runs; the rest are created through the API, which
    # refuses a control that names no modification.
    assert _named(PTMKind.CONTROL) == ["Detyrosination control"]


def test_every_seeded_control_names_the_modification_it_controls():
    # The link is by NAME here and resolved to an id after the flush: a SERIAL
    # id is not knowable while writing a literal. A name that matches no seeded
    # row would KeyError at seed time, which is the loud end of the failure.
    seeded = {p["name"] for p in DEFAULT_PTMS}
    for entry in DEFAULT_PTMS:
        if entry.get("kind") == PTMKind.CONTROL.value:
            assert entry.get("controls") in seeded, entry["name"]


def test_only_controls_carry_a_pairing():
    # A modification that "controls" something, or an `Unmodified` row that
    # does, means nothing — and the router rejects it, so the seed must not be
    # the one place that shape exists.
    strays = [
        p["name"] for p in DEFAULT_PTMS
        if p.get("controls") and p.get("kind") != PTMKind.CONTROL.value
    ]
    assert strays == []


def test_unmodified_is_seeded_as_the_absence_of_a_modification():
    # It shipped as an ordinary modification, which is a category error: it is
    # what you get when nothing was done to the lattice.
    assert _named(PTMKind.NONE) == ["Unmodified"]


def test_the_tubulin_code_itself_stays_a_set_of_modifications():
    marks = _named(PTMKind.MODIFICATION)
    assert "Detyrosination" in marks and "Polyglutamylation" in marks
    assert "Control" not in marks and "Unmodified" not in marks


def test_every_seeded_entry_declares_a_kind():
    # Leaning on the column default here would mean the seed and the column
    # disagree the moment the default moves.
    missing = [p["name"] for p in DEFAULT_PTMS if "kind" not in p]
    assert missing == []


def test_every_seeded_kind_is_one_the_client_can_draw():
    allowed = {k.value for k in PTMKind}
    assert {p["kind"] for p in DEFAULT_PTMS} <= allowed


def test_seeded_names_are_unique():
    # `ptms.name` is UNIQUE, so a duplicate here is an IntegrityError at first
    # boot on a fresh database.
    names = [p["name"] for p in DEFAULT_PTMS]
    assert len(names) == len(set(names))


# -- the constraint and the one-off script ----------------------------------


def test_the_column_is_constrained_to_kinds_a_client_can_draw():
    """Pydantic guards the API; this guards everything else.

    `scripts/ptm_control_backfill.sql` writes these values as hand-typed
    literals and is the writer that has actually run against production — a typo
    there produces exactly the silence this feature exists to prevent.
    """
    checks = [c for c in PTM.__table__.constraints if c.name == PTM_KIND_CHECK]
    assert len(checks) == 1
    sql = str(checks[0].sqltext)
    for kind in PTMKind:
        assert f"'{kind.value}'" in sql


def test_the_check_sql_is_generated_from_the_enum():
    # Hand-typing the list in the DDL is how the constraint and the enum drift.
    for kind in PTMKind:
        assert f"'{kind.value}'" in _kind_check_sql()


def test_the_backfill_script_cannot_report_success_after_failing():
    """psql walks past errors and exits 0 unless told otherwise.

    Run the script before the backend restart and the column does not exist yet:
    every statement errors, COMMIT silently becomes ROLLBACK, and the deploy log
    reads as success. Same class of lie this repo already fixed for backups.
    """
    sql = backfill_sql("ptm_control_backfill.sql").read_text()
    assert "\\set ON_ERROR_STOP on" in sql
    # And the post-condition must be an assertion, not a table for a human to
    # read: both UPDATEs match by name, so a renamed vocabulary touches zero
    # rows without erroring.
    assert "RAISE EXCEPTION" in sql


def test_the_backfill_script_classifies_a_pre_existing_control_row():
    # Before `kind` existed a lab that had already made a "Control" row had it
    # filed as a modification -- there was no other option, and `Unmodified`'s
    # own seeded description called itself "the control condition". Left there,
    # every control draws with the PTM centre dot.
    sql = backfill_sql("ptm_control_backfill.sql").read_text()
    assert "SET kind = 'control' WHERE name = 'Control'" in sql


def test_the_backfill_script_pairs_every_control_it_leaves_behind():
    # An unpaired control IS the pooled row this migration removes, so the
    # script must fail rather than leave one — reaching that state in silence is
    # the whole failure mode.
    sql = backfill_sql("ptm_control_backfill.sql").read_text()
    assert "controls_ptm_id" in sql
    assert "c.kind = 'control'" in sql and "m.kind <> 'modification'" in sql


def test_the_script_and_the_seed_agree_on_the_control_row():
    """Two copies of one vocabulary row; nothing else keeps them matching.

    A fresh install seeds from DEFAULT_PTMS and an existing one is backfilled by
    the script, so a drift here means the same row is a different colour in
    production than in dev.
    """
    seeded = next(p for p in DEFAULT_PTMS if p["kind"] == PTMKind.CONTROL.value)
    sql = backfill_sql("ptm_control_backfill.sql").read_text()
    assert f"'{seeded['name']}'" in sql
    assert f"'{seeded['color']}'" in sql
    assert f"'{seeded['abbreviation']}'" in sql
