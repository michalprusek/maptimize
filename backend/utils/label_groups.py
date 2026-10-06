"""Reader-defined classes for the separability score.

Every other label axis is a column: a point's class is whatever its protein,
microscope, PTM, cell line or experiment is. That cannot express "these two
experiments against that one", nor "MTCL1 on unmodified microtubules against
MTCL1 on detyrosinated ones" — so a caller may instead hand over the classes
themselves, each a set of **conditions**.

A group reads exactly like the facet filter (``utils/facets.py``): values of one
facet are alternatives, different facets must all hold. ``protein:5,ptm:2`` is
"protein 5 AND ptm 2"; ``experiment:180,experiment:308`` is "either experiment".
The same value may sit in any number of groups — two conditions of one protein
is the comparison this exists for.

Groups can still overlap ("MTCL1" and "MTCL1 AND detyrosinated" share every
point of the second). A point matching more than one group is reported as
``AMBIGUOUS_GROUP`` and left out of the score rather than assigned to whichever
group happened to be listed first — a silhouette over classes that share points
measures the order of a query string.
"""
from dataclasses import dataclass
from typing import Dict, FrozenSet, List, Mapping, Optional, Sequence, Set, Tuple

from utils.facets import UNASSIGNED_FACET_ID

# The facets a group member may name. Spelled as on the wire and in the filter
# panel, so a pill dragged from any section is a valid member as it stands.
GROUP_FACETS: Tuple[str, ...] = (
    "experiment",
    "microscope",
    "protein",
    "ptm",
    "cell_line",
)

# In more than one group. Never a list index, so it cannot be mistaken for one.
AMBIGUOUS_GROUP = -1

# The client has eight colours a reader can tell apart, and both caps keep the request
# inside a URL that proxies will carry.
MAX_GROUPS = 8
MAX_MEMBERS_PER_GROUP = 60

Member = Tuple[str, int]


@dataclass(frozen=True)
class LabelGroups:
    """The groups of one request, in the order the caller listed them.

    Order is the contract: a point's group is reported as an index into this
    tuple, so an empty group keeps its slot instead of being dropped.
    """

    groups: Tuple[FrozenSet[Member], ...] = ()

    def __bool__(self) -> bool:
        return bool(self.groups)


NO_GROUPS = LabelGroups()


def _parse_member(token: str) -> Member:
    facet, separator, raw_id = token.partition(":")
    if not separator or facet not in GROUP_FACETS:
        raise ValueError(
            f"group member {token!r} must be '<facet>:<id>' with facet one of "
            f"{', '.join(GROUP_FACETS)}"
        )
    # isdecimal, not int(): int() also accepts '+3', ' 3 ' and '３', and a
    # member that parses differently on the client labels points the legend
    # does not show.
    if not raw_id.isascii() or not raw_id.isdecimal():
        raise ValueError(f"group member {token!r} has a non-numeric id")
    member_id = int(raw_id)
    if facet == "experiment" and member_id == UNASSIGNED_FACET_ID:
        # experiments.id is never NULL, so "unassigned experiment" matches nothing.
        raise ValueError("experiment:0 is not a group member: every point has one")
    return facet, member_id


def parse_label_groups(raw: Optional[Sequence[str]]) -> LabelGroups:
    """Parse repeated ``group`` values, each ``facet:id,facet:id,…``.

    An empty string is an empty group and keeps its position. Raises
    ``ValueError`` on anything malformed: silently skipping a bad member would
    score a different comparison from the one that was asked for.
    """
    if not raw:
        return NO_GROUPS
    if len(raw) > MAX_GROUPS:
        raise ValueError(f"at most {MAX_GROUPS} groups, got {len(raw)}")

    groups: List[FrozenSet[Member]] = []
    for value in raw:
        tokens = [token.strip() for token in value.split(",") if token.strip()]
        if len(tokens) > MAX_MEMBERS_PER_GROUP:
            raise ValueError(
                f"at most {MAX_MEMBERS_PER_GROUP} values per group, got {len(tokens)}"
            )
        groups.append(frozenset(_parse_member(token) for token in tokens))
    return LabelGroups(tuple(groups))


def _conditions(members: FrozenSet[Member]) -> Dict[str, Set[int]]:
    """A group's members as one id set per facet: OR inside, AND across."""
    by_facet: Dict[str, Set[int]] = {}
    for facet, member_id in members:
        by_facet.setdefault(facet, set()).add(member_id)
    return by_facet


def assign_groups(
    groups: LabelGroups,
    points: Sequence[Mapping[str, int]],
) -> List[Optional[int]]:
    """The group of each point: its index, ``None``, or ``AMBIGUOUS_GROUP``.

    ``points[i]`` maps every facet in ``GROUP_FACETS`` to the id that point
    carries on it, with ``UNASSIGNED_FACET_ID`` where nothing is assigned — the
    same sentinel the filter uses, so "no PTM recorded" can be a condition.

    A point is in a group when it satisfies every facet the group names, by
    carrying any one of the values listed for that facet.
    """
    # An empty group has no conditions; it matches NOTHING, not everything —
    # it is a slot the reader has not filled yet.
    conditions = [_conditions(members) for members in groups.groups]

    assigned: List[Optional[int]] = []
    for point in points:
        hits = [
            index
            for index, wanted in enumerate(conditions)
            if wanted and all(point[facet] in ids for facet, ids in wanted.items())
        ]
        if not hits:
            assigned.append(None)
        elif len(hits) == 1:
            assigned.append(hits[0])
        else:
            assigned.append(AMBIGUOUS_GROUP)
    return assigned


def score_labels(assigned: Sequence[Optional[int]]) -> List[Optional[int]]:
    """Group assignment as separability labels: ambiguous points carry none."""
    return [None if group == AMBIGUOUS_GROUP else group for group in assigned]
