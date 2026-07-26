"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { Check, ChevronDown } from "lucide-react";

export interface ColorTagOption {
  id: number;
  name: string;
  color?: string | null;
  /** Rendered in muted parentheses after the name (manufacturer, full protein name). */
  secondary?: string | null;
}

/**
 * Adapts API records onto options. Structural on purpose — it takes anything
 * with `id`/`name`/`color` (MAP proteins, microscopes) so this module stays free
 * of domain types, and reads `secondary` through an accessor because each record
 * names that field differently (`manufacturer`, `full_name`).
 */
export function toColorTagOptions<T extends { id: number; name: string; color?: string | null }>(
  items: T[] | undefined,
  secondary?: (item: T) => string | null | undefined
): ColorTagOption[] | undefined {
  return items?.map((item) => ({
    id: item.id,
    name: item.name,
    color: item.color,
    secondary: secondary?.(item),
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

  const menuPosition = isChip ? `w-56 ${align === "right" ? "right-0" : "left-0"}` : "left-0 right-0";

  return (
    <div ref={containerRef} className={`relative ${className}`}>
      <button
        type="button"
        disabled={disabled}
        onClick={() => setOpenState(!open)}
        className={`flex items-center disabled:opacity-50 ${triggerClass}`}
        style={triggerStyle}
      >
        <span className="flex items-center gap-2">
          {selected ? (
            <>
              <span
                className="w-3 h-3 rounded-full flex-shrink-0"
                style={{ backgroundColor: selected.color || NO_COLOR }}
              />
              {/* The chip tints its label to match the dot; the field does not. */}
              <span
                className={isChip ? "font-medium" : undefined}
                style={isChip && selected.color ? { color: selected.color } : undefined}
              >
                {selected.name}
              </span>
            </>
          ) : (
            <span className="text-text-muted">{placeholder}</span>
          )}
        </span>
        <ChevronDown
          className={`w-4 h-4 text-text-muted transition-transform ${open ? "rotate-180" : ""}`}
        />
      </button>

      {open && (
        <div
          className={`absolute top-full mt-1 ${menuPosition} bg-bg-elevated border border-white/10 rounded-lg shadow-xl z-50 py-1 max-h-60 overflow-y-auto`}
        >
          {hint && (
            <div className="px-3 py-2 text-xs text-text-muted border-b border-white/10">
              {hint}
            </div>
          )}
          <button
            type="button"
            onClick={() => pick(null)}
            className="w-full px-3 py-2 text-left text-sm hover:bg-white/5 transition-colors flex items-center gap-2"
          >
            <span className="w-3 h-3 rounded-full bg-text-muted/30 flex-shrink-0" />
            <span className="text-text-muted">{clearLabel ?? placeholder}</span>
            {value === null && <Check className="w-4 h-4 ml-auto text-text-muted" />}
          </button>
          {options?.map((option) => (
            <button
              key={option.id}
              type="button"
              onClick={() => pick(option.id)}
              className={`w-full px-3 py-2 text-left text-sm hover:bg-white/5 transition-colors flex items-center gap-2 ${
                option.id === value ? "bg-white/5" : ""
              }`}
            >
              <span
                className="w-3 h-3 rounded-full flex-shrink-0"
                style={{ backgroundColor: option.color || NO_COLOR }}
              />
              <span className="text-text-primary truncate">{option.name}</span>
              {option.secondary && (
                <span className="text-xs text-text-muted truncate">({option.secondary})</span>
              )}
              {option.id === value && <Check className="w-4 h-4 ml-auto flex-shrink-0" />}
            </button>
          ))}
        </div>
      )}
    </div>
  );
}
