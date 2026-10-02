/**
 * Whether a cell line may be deleted, decided before the button is drawn.
 *
 * Kept out of the component so it can be tested without a browser, for the same
 * reason `colorTagFilter.ts` is: this one decides whether a destructive control
 * is live, and both of its failure modes are silent.
 */
import type { CellLineDetailed } from "@/lib/api";

export type DeleteVerdict =
  | { kind: "allowed" }
  | { kind: "in-use"; experiments: number }
  | { kind: "unknown" };

/**
 * Can this cell line be removed?
 *
 * ⚠️ Fails CLOSED. Anything other than "this id is in the list and its
 * `experiment_count` is zero" comes back `unknown`, which leaves the trash
 * disabled. The trap it avoids is arithmetic: a missing lookup yields
 * `undefined`, and `undefined > 0` is `false` — so the obvious spelling
 * (`count > 0 ? blocked : allowed`) arms the button for a row we know nothing
 * about, which is the one case where being wrong is unrecoverable.
 *
 * Cell lines are shared reference data with no admin page, so deleting one is
 * visible to the whole lab and cannot be undone from the UI. The backend's 409
 * is still the real guard; this is what stops the user reaching a confirmation
 * dialog for something that was never going to work.
 */
export function cellLineDeleteVerdict(
  cellLines: CellLineDetailed[] | undefined,
  id: number
): DeleteVerdict {
  const line = (cellLines ?? []).find((candidate) => candidate.id === id);
  if (!line || typeof line.experiment_count !== "number") {
    return { kind: "unknown" };
  }
  return line.experiment_count > 0
    ? { kind: "in-use", experiments: line.experiment_count }
    : { kind: "allowed" };
}
