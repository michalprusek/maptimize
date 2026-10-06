"""Reader-defined separability classes (utils/label_groups.py).

The failure this guards is quiet in every direction: a member parsed loosely, a
shared point assigned to whichever group came first, or an empty group dropped
so the indices shift — each yields a plausible score for a comparison nobody
asked for.
"""
import pytest

from utils.label_groups import (
    AMBIGUOUS_GROUP,
    MAX_GROUPS,
    MAX_MEMBERS_PER_GROUP,
    NO_GROUPS,
    assign_groups,
    parse_label_groups,
    score_labels,
)


def _point(experiment=1, protein=0, microscope=0, ptm=0, cell_line=0):
    return {
        "experiment": experiment, "protein": protein, "microscope": microscope,
        "ptm": ptm, "cell_line": cell_line,
    }


# -- parsing ------------------------------------------------------------------
def test_nothing_sent_is_no_groups():
    assert parse_label_groups(None) is NO_GROUPS
    assert parse_label_groups([]) is NO_GROUPS
    assert not NO_GROUPS


def test_parses_mixed_facets_in_the_order_given():
    parsed = parse_label_groups(["experiment:180,experiment:308", "microscope:1, ptm:0"])
    assert parsed.groups == (
        frozenset({("experiment", 180), ("experiment", 308)}),
        frozenset({("microscope", 1), ("ptm", 0)}),
    )


def test_an_empty_group_keeps_its_slot():
    # The client reports a point's group as an index into ITS list. Dropping an
    # empty group would shift every later index onto the wrong legend entry.
    parsed = parse_label_groups(["", "protein:3"])
    assert parsed.groups == (frozenset(), frozenset({("protein", 3)}))


@pytest.mark.parametrize(
    "bad",
    [
        "bundleness:3",      # not a facet
        "protein",           # no id
        "protein:",          # empty id
        "protein:abc",
        "protein:-1",
        "protein:+3",        # int() would accept these three
        "protein: 3",
        "protein:３",
        "protein:1.0",
        "experiment:0",      # experiments are never unassigned
    ],
)
def test_malformed_members_are_refused_not_skipped(bad):
    with pytest.raises(ValueError):
        parse_label_groups(["protein:1", bad])


def test_caps_are_enforced_at_the_boundary():
    assert len(parse_label_groups(["protein:1"] * MAX_GROUPS).groups) == MAX_GROUPS
    with pytest.raises(ValueError, match="groups"):
        parse_label_groups(["protein:1"] * (MAX_GROUPS + 1))

    full = ",".join(f"experiment:{i}" for i in range(1, MAX_MEMBERS_PER_GROUP + 1))
    assert len(parse_label_groups([full]).groups[0]) == MAX_MEMBERS_PER_GROUP
    with pytest.raises(ValueError, match="values per group"):
        parse_label_groups([full + ",experiment:9999"])


# -- assignment ---------------------------------------------------------------
def test_values_of_one_facet_are_alternatives():
    groups = parse_label_groups(["experiment:180,experiment:308", "experiment:315"])
    points = [_point(180), _point(308), _point(315), _point(999)]
    assert assign_groups(groups, points) == [0, 0, 1, None]


def test_a_point_in_two_groups_is_ambiguous_not_first_wins():
    # MAP2d (protein 3) vs everything on 3D SIM (microscope 1): a MAP2d crop
    # taken on 3D SIM is in both. Listing order must not decide its class.
    groups = parse_label_groups(["protein:3", "microscope:1"])
    reversed_groups = parse_label_groups(["microscope:1", "protein:3"])
    points = [
        _point(protein=3, microscope=2),
        _point(protein=5, microscope=1),
        _point(protein=3, microscope=1),
    ]
    assert assign_groups(groups, points) == [0, 1, AMBIGUOUS_GROUP]
    assert assign_groups(reversed_groups, points) == [1, 0, AMBIGUOUS_GROUP]


def test_facets_inside_a_group_must_all_hold():
    # The comparison this exists for: one protein under two PTM conditions.
    groups = parse_label_groups(["protein:3,ptm:1", "protein:3,ptm:2"])
    points = [
        _point(protein=3, ptm=1),
        _point(protein=3, ptm=2),
        _point(protein=3, ptm=7),  # right protein, neither condition
        _point(protein=5, ptm=1),  # right condition, wrong protein
    ]
    assert assign_groups(groups, points) == [0, 1, None, None]


def test_values_of_one_facet_are_alternatives_within_the_conjunction():
    groups = parse_label_groups(["protein:3,protein:5,ptm:1"])
    points = [
        _point(protein=3, ptm=1),
        _point(protein=5, ptm=1),
        _point(protein=5, ptm=2),
        _point(protein=8, ptm=1),
    ]
    assert assign_groups(groups, points) == [0, 0, None, None]


def test_a_broader_group_overlapping_a_narrower_one_is_ambiguous():
    # "MTCL1" and "MTCL1 AND detyrosinated" share every point of the second.
    groups = parse_label_groups(["protein:3", "protein:3,ptm:2"])
    points = [_point(protein=3, ptm=1), _point(protein=3, ptm=2)]
    assert assign_groups(groups, points) == [0, AMBIGUOUS_GROUP]


def test_an_empty_group_matches_nothing_not_everything():
    # No conditions is an unfilled slot. `all()` of nothing is True, so the
    # naive reading would sweep every point into it.
    groups = parse_label_groups(["", "protein:3"])
    assert assign_groups(groups, [_point(protein=3), _point(protein=5)]) == [1, None]


def test_unassigned_is_a_value_a_group_can_name():
    groups = parse_label_groups(["ptm:0", "ptm:2"])
    assert assign_groups(groups, [_point(ptm=0), _point(ptm=2), _point(ptm=7)]) == [0, 1, None]


def test_facets_do_not_bleed_into_each_other():
    # protein 3 and microscope 3 are different things that share a number.
    groups = parse_label_groups(["protein:3"])
    assert assign_groups(groups, [_point(protein=1, microscope=3)]) == [None]


def test_no_groups_assigns_nothing():
    assert assign_groups(NO_GROUPS, [_point(), _point()]) == [None, None]


def test_ambiguous_points_carry_no_label_into_the_score():
    assert score_labels([0, 1, AMBIGUOUS_GROUP, None, 0]) == [0, 1, None, None, 0]
