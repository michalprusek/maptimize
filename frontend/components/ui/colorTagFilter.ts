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
