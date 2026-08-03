"""The seeded PTM vocabulary, and the two entries that are not modifications.

`seed_default_data()` fires only on an empty table, so these values are what a
fresh install and the test database start from. The marker channel on the
projections is driven entirely by `kind`, so a seed that shipped every row as
`modification` would draw every point identically with nothing failing anywhere
— which is precisely the failure this file exists to make loud.
"""
from models.ptm import DEFAULT_PTMS, PTMKind


def _named(kind: PTMKind) -> list[str]:
    return [p["name"] for p in DEFAULT_PTMS if p.get("kind") == kind.value]


def test_the_seed_offers_exactly_one_control():
    # The lab runs an inactive-enzyme control alongside every PTM condition, so
    # this is a value they reach for constantly — it belongs in the seed, not in
    # a row each person creates for themselves with a different name.
    assert _named(PTMKind.CONTROL) == ["Control"]


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
