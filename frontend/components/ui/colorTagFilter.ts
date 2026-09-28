/**
 * Typed-name matching for a `ColorTagSelect` that can create.
 *
 * Kept out of the component so it can be tested without a browser, for the same
 * reason `umapFacets.ts` is: getting this wrong produces no error anywhere. A
 * picker that offers "create" for a name it already holds mints a duplicate, and
 * a duplicate reference row shows up only as two dashboard facet pills that
 * quietly never compare.
 */
import type { ColorTagOption } from "./ColorTagSelect";

/** What two names are compared as: trimmed and case-folded. */
function normalise(name: string): string {
  return name.trim().toLowerCase();
}

/**
 * Options whose name contains the query, ignoring case and surrounding space.
 *
 * A blank query keeps everything — an untouched search box means "no filter",
 * never "no matches".
 */
export function filterColorTagOptions(
  options: ColorTagOption[] | undefined,
  query: string
): ColorTagOption[] {
  const needle = normalise(query);
  if (!needle) return options ?? [];
  return (options ?? []).filter((option) =>
    option.name.toLowerCase().includes(needle)
  );
}

/**
 * The name to offer creating, or null when there is nothing to create.
 *
 * ⚠️ The comparison is case-folded and trimmed, and it is an EQUALITY check, not
 * the substring test above. Folding is what stops "u2os" being offered as a new
 * line beside "U2OS"; equality is what keeps "HEK" creatable even though it
 * filters down to "HEK293T".
 *
 * `undefined` options mean the list has not loaded, not that it is empty. The
 * name is still offered: the backend refuses a duplicate either way, and
 * silently withholding the only create affordance would be a dead end with
 * nothing shown to explain it.
 */
export function creatableName(
  options: ColorTagOption[] | undefined,
  query: string
): string | null {
  const typed = query.trim();
  if (!typed) return null;

  const needle = normalise(typed);
  const exists = (options ?? []).some(
    (option) => normalise(option.name) === needle
  );
  return exists ? null : typed;
}

/** What pressing Enter in the search box should do, or null for "nothing". */
export type EnterAction =
  | { kind: "select"; id: number }
  | { kind: "create"; name: string };

/**
 * Resolve Enter in the search box.
 *
 * ⚠️ Enter must prefer SELECTING. The first version only ever created, and the
 * two predicates around it do not agree on what "matches": `creatableName` is
 * an equality check while `filterColorTagOptions` is a substring check. So
 * typing "u2" listed U2OS in the menu and Enter minted a shared cell line
 * literally named "u2" — visible to the whole lab, a permanent extra facet
 * pill, and with no admin page to delete it from. Enter over an exact existing
 * name meanwhile did nothing at all.
 *
 * The rule: one visible option means Enter picks it; no visible options and
 * something typed means Enter creates it; anything else is a guess, so Enter
 * does nothing and waits for a click. Creating a name that is a prefix of an
 * existing line ("HEK" beside "HEK293T") stays possible — through the create
 * row, which is a deliberate click rather than a keystroke.
 */
export function enterAction(
  options: ColorTagOption[] | undefined,
  query: string
): EnterAction | null {
  if (!query.trim()) return null;

  const visible = filterColorTagOptions(options, query);
  if (visible.length === 1) return { kind: "select", id: visible[0].id };
  if (visible.length > 1) return null;

  const name = creatableName(options, query);
  return name ? { kind: "create", name } : null;
}
