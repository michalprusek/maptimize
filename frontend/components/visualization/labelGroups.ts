/**
 * Reader-defined classes for the separability score.
 *
 * Every colour-by dimension is a column: a point's class is its protein, its
 * microscope, and so on. That cannot express "these two experiments against
 * that one", nor "MTCL1 unmodified against MTCL1 detyrosinated", so the reader
 * may instead build the classes by hand. Each group is a set of CONDITIONS that
 * reads exactly like the filter: values of one facet are alternatives, different
 * facets must all hold. A value may sit in any number of groups.
 *
 * Pure functions only. Which group a point is in is decided by the BACKEND and
 * arrives on the point (`point.group`); nothing here re-derives it, so the
 * colours on the plot and the score in the badge come from one assignment.
 */
import { FACET_ORDER, type FacetKey } from "./umapFacets";

export interface GroupMember {
  facet: FacetKey;
  id: number;
}

export interface LabelGroup {
  name: string;
  members: GroupMember[];
}

/** `point.group` for a point that falls in more than one group. */
export const AMBIGUOUS_GROUP = -1;

/** Mirrors `MAX_GROUPS` / `MAX_MEMBERS_PER_GROUP` in utils/label_groups.py. */
export const MAX_GROUPS = 8;
export const MAX_MEMBERS_PER_GROUP = 60;
export const MAX_GROUP_NAME_LENGTH = 40;

/**
 * One colour per group slot: the Okabe–Ito colour-blind-safe set without its
 * black (invisible on this background), plus an olive from Paul Tol's muted
 * scheme to reach eight. Grey is deliberately absent — it means "in no group".
 *
 * By slot, not by content, so a group keeps its colour while values are dragged
 * in and out of it.
 */
export const GROUP_COLORS: readonly string[] = [
  "#E69F00",
  "#56B4E9",
  "#009E73",
  "#F0E442",
  "#0072B2",
  "#D55E00",
  "#CC79A7",
  "#999933",
];

export function groupColor(index: number): string {
  return GROUP_COLORS[index % GROUP_COLORS.length];
}

function sameMember(a: GroupMember, b: GroupMember): boolean {
  return a.facet === b.facet && a.id === b.id;
}

/** Every group that names this value, in slot order. */
export function groupsOfMember(groups: LabelGroup[], member: GroupMember): number[] {
  return groups.flatMap((group, index) =>
    group.members.some((existing) => sameMember(existing, member)) ? [index] : []
  );
}

/**
 * Put a value into a group. Other groups keep theirs.
 *
 * A copy, not a move: one protein under two conditions needs that protein in
 * both groups. Returns the input unchanged when the target does not exist, is
 * full, or already holds the value.
 */
export function addMember(
  groups: LabelGroup[],
  index: number,
  member: GroupMember
): LabelGroup[] {
  const target = groups[index];
  if (!target) return groups;
  if (target.members.some((existing) => sameMember(existing, member))) return groups;
  if (target.members.length >= MAX_MEMBERS_PER_GROUP) return groups;

  return groups.map((group, at) =>
    at === index ? { ...group, members: [...group.members, member] } : group
  );
}

export interface GroupCondition {
  facet: FacetKey;
  /** Alternatives: a point needs any one of them. */
  members: GroupMember[];
}

/**
 * A group as the conditions it imposes, one per facet it names, in the filter
 * panel's facet order. A point is in the group when it meets ALL of them.
 */
export function groupConditions(group: LabelGroup): GroupCondition[] {
  return FACET_ORDER.map((facet) => ({
    facet,
    members: group.members.filter((member) => member.facet === facet),
  })).filter((condition) => condition.members.length > 0);
}

/** Append a group, optionally seeded with one value. */
export function addGroup(
  groups: LabelGroup[],
  name: string,
  member?: GroupMember
): LabelGroup[] {
  if (groups.length >= MAX_GROUPS) return groups;
  const appended = [...groups, { name: cleanName(name), members: [] }];
  return member ? addMember(appended, appended.length - 1, member) : appended;
}

export function removeMember(
  groups: LabelGroup[],
  index: number,
  member: GroupMember
): LabelGroup[] {
  return groups.map((group, at) =>
    at === index
      ? {
          ...group,
          members: group.members.filter((existing) => !sameMember(existing, member)),
        }
      : group
  );
}

export function removeGroup(groups: LabelGroup[], index: number): LabelGroup[] {
  return groups.filter((_, at) => at !== index);
}

export function renameGroup(
  groups: LabelGroup[],
  index: number,
  name: string
): LabelGroup[] {
  return groups.map((group, at) =>
    at === index ? { ...group, name: name.slice(0, MAX_GROUP_NAME_LENGTH) } : group
  );
}

function cleanName(name: string): string {
  return name.trim().slice(0, MAX_GROUP_NAME_LENGTH);
}

function memberToken(member: GroupMember): string {
  return `${member.facet}:${member.id}`;
}

/**
 * One `facet:id,…` string per group, as the API takes them.
 *
 * Which members are alternatives and which must all hold is read off their
 * facets by the backend, so the string carries no operators.
 *
 * Members are sorted so the same group dragged together in a different order is
 * the same request (and the same cache entry). The GROUPS are not: their order
 * is what `point.group` indexes into. Empty groups stay, as empty strings.
 */
export function groupsToWire(groups: LabelGroup[]): string[] {
  return groups.map((group) => group.members.map(memberToken).sort().join(","));
}

function parseMember(token: string): GroupMember | null {
  const [facet, rawId, ...rest] = token.split(":");
  if (rest.length > 0 || !FACET_ORDER.includes(facet as FacetKey)) return null;
  // Digits only. `Number("")` is 0 — the "unassigned" sentinel — so a looser
  // parse would turn a truncated link into a group of unassigned points.
  if (!/^\d+$/.test(rawId ?? "")) return null;
  const id = Number(rawId);
  // The experiment column is never null, and the backend rejects experiment:0.
  if (facet === "experiment" && id === 0) return null;
  return { facet: facet as FacetKey, id };
}

/** URL parameter carrying the groups: one `name|facet:id,…` per group. */
const GROUP_PARAM = "g";

/**
 * Read groups out of a URL query string, ignoring anything malformed.
 *
 * The name is everything before the LAST `|`, so a name may itself contain one;
 * members never do.
 */
export function groupsFromQuery(search: string): LabelGroup[] {
  let groups: LabelGroup[] = [];
  for (const raw of new URLSearchParams(search).getAll(GROUP_PARAM)) {
    if (groups.length >= MAX_GROUPS) break;
    const cut = raw.lastIndexOf("|");
    if (cut === -1) continue;

    groups = addGroup(groups, raw.slice(0, cut));
    for (const token of raw.slice(cut + 1).split(",")) {
      const member = parseMember(token.trim());
      // Through addMember, so a hand-edited link cannot repeat a value inside
      // a group or overfill one.
      if (member) groups = addMember(groups, groups.length - 1, member);
    }
  }
  return groups;
}

/** Write the groups into an existing query string, leaving other params alone. */
export function groupsToQuery(groups: LabelGroup[], search = ""): string {
  const params = new URLSearchParams(search);
  params.delete(GROUP_PARAM);
  const wire = groupsToWire(groups);
  groups.forEach((group, index) => {
    params.append(GROUP_PARAM, `${group.name}|${wire[index]}`);
  });
  return params.toString();
}

export interface GroupCounts {
  /** Points in each group, positionally. */
  perGroup: number[];
  /** Points in more than one group — drawn grey and left out of the score. */
  ambiguous: number;
  ungrouped: number;
}

/** Tally the backend's assignment over the points on screen. */
export function groupCounts(
  assigned: Array<number | null | undefined>,
  groupCount: number
): GroupCounts {
  const counts: GroupCounts = {
    perGroup: new Array(groupCount).fill(0),
    ambiguous: 0,
    ungrouped: 0,
  };
  for (const group of assigned) {
    if (group === AMBIGUOUS_GROUP) counts.ambiguous++;
    else if (group == null || group < 0 || group >= groupCount) counts.ungrouped++;
    else counts.perGroup[group]++;
  }
  return counts;
}

/**
 * Does this edit move a group into a different slot?
 *
 * `point.group` is a slot index, and the chart keeps the previous response on
 * screen while the next one loads. Renaming, adding a value and appending a
 * group all leave every existing index meaning the same slot; DELETING a group
 * shifts the later ones, so each old index would name its neighbour, in its
 * neighbour's colour.
 *
 * The caller bumps an epoch on a shift and reads indices only from a response
 * fetched in the current epoch. Comparing slot COUNTS instead is not enough:
 * delete one group and add another before the refetch lands and the count is
 * back where it started, with every later slot holding a different group.
 */
export function shiftsGroupSlots(previous: LabelGroup[], next: LabelGroup[]): boolean {
  return next.length < previous.length;
}

/** MIME type for a facet value being dragged out of the filter panel. */
export const MEMBER_MIME = "application/x-maptimize-facet-value";

export function encodeMember(member: GroupMember): string {
  return memberToken(member);
}

/** Null for anything that is not one of our pills (a dragged file, a link…). */
export function decodeMember(payload: string): GroupMember | null {
  return parseMember(payload);
}
