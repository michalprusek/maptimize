import { expect, test } from "@playwright/test";

import {
  creatableName,
  filterColorTagOptions,
} from "../../components/ui/colorTagFilter";
import type { ColorTagOption } from "../../components/ui/ColorTagSelect";
import en from "../../messages/en.json";
import fr from "../../messages/fr.json";

/**
 * The typed-name half of the cell-line picker.
 *
 * Cell lines have no admin page: a line comes into existence because someone
 * typed a name into the experiment form that the list did not hold. So these
 * two functions decide, on every keystroke, whether the lab is *picking* U2OS
 * or *minting a second one* — and the failure is silent either way. A split
 * population just shows up as two facet pills that never compare.
 */

const OPTIONS: ColorTagOption[] = [
  { id: 1, name: "U2OS", color: "#3b82f6" },
  { id: 2, name: "HeLa", color: "#ef4444" },
  { id: 3, name: "HEK293T", color: "#22c55e" },
];

test.describe("filterColorTagOptions", () => {
  test("matches on a substring, ignoring case", () => {
    expect(filterColorTagOptions(OPTIONS, "he").map((o) => o.name)).toEqual([
      "HeLa",
      "HEK293T",
    ]);
  });

  test("an empty or blank query keeps every option", () => {
    expect(filterColorTagOptions(OPTIONS, "")).toHaveLength(3);
    expect(filterColorTagOptions(OPTIONS, "   ")).toHaveLength(3);
  });

  test("ignores whitespace around what was typed", () => {
    expect(filterColorTagOptions(OPTIONS, "  u2os ").map((o) => o.name)).toEqual([
      "U2OS",
    ]);
  });

  test("an undefined list is empty, not a crash", () => {
    // The options come from a react-query result that is undefined while it
    // loads, and the picker renders through that first frame.
    expect(filterColorTagOptions(undefined, "u2os")).toEqual([]);
  });
});

test.describe("creatableName", () => {
  test("offers a name the list does not hold", () => {
    expect(creatableName(OPTIONS, "COS-7")).toBe("COS-7");
  });

  test("offers the trimmed name, not what was typed", () => {
    // A trailing space is invisible in the box and would be the only thing
    // distinguishing the new row from the one already there.
    expect(creatableName(OPTIONS, "  COS-7  ")).toBe("COS-7");
  });

  test("offers nothing for a name that already exists", () => {
    expect(creatableName(OPTIONS, "U2OS")).toBeNull();
  });

  test("offers nothing for a case variant of an existing name", () => {
    // THE case this exists for. "u2os" differs from "U2OS" as a string, so
    // without folding here the picker invites a create that the backend then
    // refuses with a 400 — and before the backend also folded, accepted,
    // splitting the corpus in two.
    expect(creatableName(OPTIONS, "u2os")).toBeNull();
    expect(creatableName(OPTIONS, "hela")).toBeNull();
    expect(creatableName(OPTIONS, " HEK293t ")).toBeNull();
  });

  test("offers nothing for an empty or blank query", () => {
    expect(creatableName(OPTIONS, "")).toBeNull();
    expect(creatableName(OPTIONS, "   ")).toBeNull();
  });

  test("offers the name when the list has not loaded yet", () => {
    // Undefined is "not loaded", not "empty". Suppressing the create row would
    // be safer-looking, but the list always loads before anyone can type — and
    // if it failed, refusing to create is a dead end with no error shown.
    expect(creatableName(undefined, "COS-7")).toBe("COS-7");
  });

  test("a partial match is still creatable", () => {
    // "HEK" filters down to HEK293T, but it is not that line. Treating a
    // substring hit as "already exists" would make short names unenterable.
    expect(creatableName(OPTIONS, "HEK")).toBe("HEK");
  });
});

test.describe("the create row's message", () => {
  for (const [locale, messages] of Object.entries({ en, fr })) {
    test(`carries the {name} placeholder in ${locale}.json`, () => {
      // The picker passes `(name) => t("createCellLine", { name })`, and
      // next-intl leaves an unknown placeholder in place rather than throwing.
      // Renaming it therefore shows the lab a literal "{name}" on the one row
      // that is supposed to echo what they just typed.
      expect(messages.experiments.createCellLine).toContain("{name}");
    });

    test(`the search placeholder is translated in ${locale}.json`, () => {
      expect(messages.experiments.searchOrTypeCellLine).toBeTruthy();
    });
  }
});
