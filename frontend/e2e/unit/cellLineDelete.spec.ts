import { expect, test } from "@playwright/test";

import { cellLineDeleteVerdict } from "../../components/experiment/cellLineDelete";
import en from "../../messages/en.json";
import fr from "../../messages/fr.json";
import type { CellLineDetailed } from "../../lib/api";

/**
 * Whether the trash beside a cell line is live.
 *
 * Cell lines are shared reference data with no admin page, so this is the only
 * delete affordance in the product — and the row it removes is visible to the
 * whole lab. The decision therefore has to fail CLOSED: every answer other than
 * "this line is definitely unused" leaves the button disabled.
 *
 * The backend refuses with 409 regardless, but a 409 arrives after the user has
 * confirmed a destructive action, which is the pattern CLAUDE.md records as a
 * mistake elsewhere ("dřív ho nabízela všem a 403 přišla až po kliknutí").
 */

const LINES: CellLineDetailed[] = [
  { id: 1, name: "U2OS", experiment_count: 181 },
  { id: 7, name: "cos", experiment_count: 0 },
];

test.describe("cellLineDeleteVerdict", () => {
  test("an unused line may be deleted", () => {
    expect(cellLineDeleteVerdict(LINES, 7)).toEqual({ kind: "allowed" });
  });

  test("a line in use reports how many experiments hold it", () => {
    // The count is what makes the disabled button explain itself.
    expect(cellLineDeleteVerdict(LINES, 1)).toEqual({
      kind: "in-use",
      experiments: 181,
    });
  });

  test("an id the list does not hold is NOT deletable", () => {
    // ⚠️ The fail-open trap: a missing lookup yields `undefined`, and
    // `undefined > 0` is false — which would read as "unused, go ahead" and arm
    // a destructive button for a row we know nothing about.
    expect(cellLineDeleteVerdict(LINES, 999)).toEqual({ kind: "unknown" });
  });

  test("a list that has not loaded yet is NOT deletable", () => {
    // Undefined is "not loaded", not "empty". Every option would otherwise look
    // unused during the first render.
    expect(cellLineDeleteVerdict(undefined, 7)).toEqual({ kind: "unknown" });
    expect(cellLineDeleteVerdict([], 7)).toEqual({ kind: "unknown" });
  });

  test("a count the API did not send is NOT deletable", () => {
    // A row without `experiment_count` is the same unknown, arriving by a
    // different route — an older cached response, say.
    const partial = [{ id: 7, name: "cos" } as CellLineDetailed];
    expect(cellLineDeleteVerdict(partial, 7)).toEqual({ kind: "unknown" });
  });
});

test.describe("the delete strings", () => {
  for (const [locale, messages] of Object.entries({ en, fr })) {
    test(`exist in ${locale}.json`, () => {
      const e = messages.experiments as Record<string, string>;
      for (const key of [
        "deleteCellLine",
        "deleteCellLineConfirm",
        "deleteCellLineInUse",
        "deleteCellLineError",
      ]) {
        expect(e[key], `${key} in ${locale}`).toBeTruthy();
      }
    });

    test(`the in-use reason carries its {count} placeholder in ${locale}.json`, () => {
      // next-intl leaves an unknown placeholder in place rather than throwing,
      // so a renamed one shows the lab a literal "{count}" on the tooltip that
      // is supposed to say why the button is dead.
      expect(messages.experiments.deleteCellLineInUse).toContain("{count}");
    });
  }
});
