"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { Check, ChevronDown } from "lucide-react";

export interface ColorTagOption {
  id: number;
  name: string;
  color?: string | null;
  /**
   * A muted second line under the name (manufacturer, full protein name, PTM
   * abbreviation, the folder above this one). Its own line on purpose -- beside
   * the name it competes with it for the row, and wins whenever it is the
   * longer of the two.
   */
  secondary?: string | null;
  /**
   * The unabbreviated `secondary`, when what is rendered is a shortened form.
   * Defaults to `secondary`.
   *
   * A folder path renders as `… / controles` so the row stays readable, and
   * that elision is lossy -- two folders under different grandparents can
   * produce the same string. Without the whole thing somewhere, the rows are
   * not merely cramped, they are indistinguishable, and this picker files
   * experiments.
   */
  secondaryFull?: string | null;
}

/**
 * Adapts API records onto options. Structural on purpose — it takes anything
 * with `id`/`name`/`color` (MAP proteins, microscopes) so this module stays free
 * of domain types, and reads `secondary` through an accessor because each record
 * names that field differently (`manufacturer`, `full_name`).
 */
export function toColorTagOptions<T extends { id: number; name: string; color?: string | null }>(
  items: T[] | undefined,
  secondary?: (item: T) => string | null | undefined,
  secondaryFull?: (item: T) => string | null | undefined
): ColorTagOption[] | undefined {
  return items?.map((item) => ({
    id: item.id,
    name: item.name,
    color: item.color,
    secondary: secondary?.(item),
    secondaryFull: secondaryFull?.(item),
  }));
}

interface ColorTagSelectProps {
  options: ColorTagOption[] | undefined;
  value: number | null;
  onChange: (id: number | null) => void;
  /** Shown on the trigger while nothing is selected. */
  placeholder: string;
  /**
   * Label for the row that clears the selection. Defaults to `placeholder`,
   * which only reads well when the placeholder states a condition ("No
   * microscope"). Pass this when the trigger instead issues an invitation
   * ("Assign MAP Protein") -- as a menu row that would be an imperative
   * masquerading as the current state.
   */
  clearLabel?: string;
  /** "field" = form control filling its container; "chip" = inline tinted pill. */
  variant?: "field" | "chip";
  /** Chip density. Ignored by the "field" variant. */
  size?: "sm" | "md";
  /** Explanatory line above the options. */
  hint?: string;
  align?: "left" | "right";
  disabled?: boolean;
  /**
   * Fires when the menu opens or closes. Needed by callers whose ancestor
   * creates a stacking context (a framer-motion card in a grid): the menu is
   * positioned against that ancestor, so the caller has to lift it above its
   * siblings while the menu is open or later cards paint over it.
   */
  onOpenChange?: (open: boolean) => void;
  className?: string;
}

/** Fallback dot colour for records that have none assigned yet. */
const NO_COLOR = "#888";

/**
 * The whole of an option, for a `title`. Every label here is abbreviated
 * somewhere -- the chip against its own ceiling, the field against its
 * container, the menu rows against the menu, and `secondary` itself may already
 * be a shortened form -- so the full text has to stay reachable on hover rather
 * than being simply lost.
 */
export function fullLabel(option: ColorTagOption): string {
  const detail = option.secondaryFull ?? option.secondary;
  return detail ? `${option.name} — ${detail}` : option.name;
}

/**
 * Dropdown for picking one colour-tagged record (MAP protein, microscope).
 *
 * SSOT for this control: it previously existed as four hand-rolled copies that
 * had each drifted (own click-outside effect, own "unassigned" wording, one
 * without a check mark on the selected row).
 */
export function ColorTagSelect({
  options,
  value,
  onChange,
  placeholder,
  clearLabel,
  variant = "field",
  size = "md",
  hint,
  align = "left",
  disabled = false,
  onOpenChange,
  className = "",
}: ColorTagSelectProps): JSX.Element {
  const [open, setOpen] = useState(false);
  const containerRef = useRef<HTMLDivElement>(null);

  // `onOpenChange` is only ever called, never depended on: latching it in a ref
  // keeps `setOpenState` stable, so the document listeners below are not torn
  // down and re-subscribed every time the parent re-renders with a new inline
  // callback -- and the effect still gets the current callback, not a stale one.
  const onOpenChangeRef = useRef(onOpenChange);
  useEffect(() => {
    onOpenChangeRef.current = onOpenChange;
  });

  const setOpenState = useCallback((next: boolean) => {
    setOpen(next);
    onOpenChangeRef.current?.(next);
  }, []);

  useEffect(() => {
    if (!open) return;

    const closeOnOutsideClick = (event: MouseEvent) => {
      if (!containerRef.current?.contains(event.target as Node)) {
        setOpenState(false);
      }
    };
    const closeOnEscape = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        setOpenState(false);
      }
    };

    document.addEventListener("mousedown", closeOnOutsideClick);
    document.addEventListener("keydown", closeOnEscape);
    return () => {
      document.removeEventListener("mousedown", closeOnOutsideClick);
      document.removeEventListener("keydown", closeOnEscape);
    };
  }, [open, setOpenState]);

  const pick = (id: number | null) => {
    onChange(id);
    setOpenState(false);
  };

  const selected = options?.find((o) => o.id === value) ?? null;
  const isChip = variant === "chip";

  const chipSurface = selected
    ? "border border-white/10 hover:border-white/20"
    : "bg-bg-secondary hover:bg-bg-hover";
  const chipPadding = size === "sm" ? "px-2.5 py-1 text-xs" : "px-4 py-2";

  const triggerClass = isChip
    ? `gap-2 rounded-lg transition-all ${chipPadding} ${chipSurface}`
    : "input-field justify-between text-left";
  const triggerStyle =
    isChip && selected
      ? {
          backgroundColor: `${selected.color || NO_COLOR}15`,
          borderColor: `${selected.color || NO_COLOR}40`,
        }
      : undefined;

  // The chip's menu is not tied to the trigger's width (a chip is as wide as its
  // own label), so it sizes to its longest row between a floor and a ceiling
  // rather than sitting at a fixed 224px that long folder paths cannot fit. The
  // field variant spans its trigger, which is already the width of the form.
  //
  // The ceiling bounds the menu's WIDTH, not where it lands: a `left-0` menu on
  // a chip far into the page still grows past the right edge, which is why the
  // callers deep in a card pass `align="right"`.
  const menuPosition = isChip
    ? `w-max min-w-56 max-w-[min(22rem,calc(100vw-2rem))] ${
        align === "right" ? "right-0" : "left-0"
      }`
    : "left-0 right-0";

  return (
    <div ref={containerRef} className={`relative ${className}`}>
      <button
        type="button"
        disabled={disabled}
        onClick={() => setOpenState(!open)}
        className={`flex items-center disabled:opacity-50 ${triggerClass}`}
        style={triggerStyle}
        title={selected ? fullLabel(selected) : placeholder}
      >
        {/* `min-w-0` so a long label truncates rather than pushing the chevron
            out of the field. It does nothing for the chip, which is sized by its
            own label -- that one is held by the ceiling below. */}
        <span className="flex items-center gap-2 min-w-0">
          {selected ? (
            <>
              <span
                className="w-3 h-3 rounded-full flex-shrink-0"
                style={{ backgroundColor: selected.color || NO_COLOR }}
              />
              {/* The chip tints its label to match the dot; the field does not.
                  A chip is sized by its own label, so it needs a ceiling of its
                  own; the field already has one from its container. */}
              <span
                className={`truncate ${isChip ? "font-medium max-w-[12rem]" : ""}`}
                style={isChip && selected.color ? { color: selected.color } : undefined}
              >
                {selected.name}
              </span>
            </>
          ) : (
            <span className="text-text-muted truncate">{placeholder}</span>
          )}
        </span>
        <ChevronDown
          className={`w-4 h-4 flex-shrink-0 text-text-muted transition-transform ${open ? "rotate-180" : ""}`}
        />
      </button>

      {open && (
        <div
          className={`absolute top-full mt-1 ${menuPosition} bg-bg-elevated border border-white/10 rounded-lg shadow-xl z-50 py-1 max-h-60 overflow-y-auto`}
        >
          {/* Capped at the menu's floor so the hint wraps instead of setting the
              width for everything below it. `w-max` sizes the menu to its
              widest child, and a sentence that never truncates resolves to its
              full single-line width -- so without this the hint, not the
              options, decided how wide the menu was, and more so in French. */}
          {hint && (
            <div className="px-3 py-2 text-xs text-text-muted border-b border-white/10 max-w-56">
              {hint}
            </div>
          )}
          <button
            type="button"
            onClick={() => pick(null)}
            title={clearLabel ?? placeholder}
            className="w-full px-3 py-2 text-left text-sm hover:bg-white/5 transition-colors flex items-center gap-2"
          >
            <span className="w-3 h-3 rounded-full bg-text-muted/30 flex-shrink-0" />
            {/* Truncates like every other row, and carries the same `title`.
                This is the row that UNASSIGNS -- misreading it cascades a NULL
                through every image and crop -- so it is the last one that should
                be left clipped with no way to read it. */}
            <span className="text-text-muted truncate flex-1">
              {clearLabel ?? placeholder}
            </span>
            {value === null && <Check className="w-4 h-4 flex-shrink-0 text-text-muted" />}
          </button>
          {options?.map((option) => (
            <button
              key={option.id}
              type="button"
              onClick={() => pick(option.id)}
              title={fullLabel(option)}
              className={`w-full px-3 py-2 text-left text-sm hover:bg-white/5 transition-colors flex items-center gap-2 ${
                option.id === value ? "bg-white/5" : ""
              }`}
            >
              <span
                className="w-3 h-3 rounded-full flex-shrink-0"
                style={{ backgroundColor: option.color || NO_COLOR }}
              />
              {/* Stacked, not side by side. Sharing one line, the name and the
                  secondary both truncate, and flexbox shrinks them in
                  proportion to their length -- so a short name beside a long
                  path collapses to a character or two while the path keeps most
                  of the row. The name is what is being picked, and it was the
                  one disappearing. `min-w-0` lets the column truncate inside
                  the row instead of forcing the row wider. */}
              <span className="flex flex-col min-w-0 flex-1">
                <span className="text-text-primary truncate">{option.name}</span>
                {option.secondary && (
                  <span className="text-xs text-text-muted truncate">{option.secondary}</span>
                )}
              </span>
              {option.id === value && <Check className="w-4 h-4 flex-shrink-0" />}
            </button>
          ))}
        </div>
      )}
    </div>
  );
}
