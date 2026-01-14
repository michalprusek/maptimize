"use client";

import { Check } from "lucide-react";

interface SelectionCheckboxProps {
  isSelected: boolean;
  onClick: (e: React.MouseEvent) => void;
  /** Whether the checkbox is always visible or only on hover */
  alwaysVisible?: boolean;
}

/**
 * A reusable selection checkbox for gallery items.
 * Positioned absolutely in the top-left corner of its container.
 */
export function SelectionCheckbox({
  isSelected,
  onClick,
  alwaysVisible = false,
}: SelectionCheckboxProps): JSX.Element {
  const visibilityClass = isSelected || alwaysVisible ? "" : "opacity-0 group-hover:opacity-100";
  const selectedClass = isSelected
    ? "bg-primary-500 border-primary-500"
    : "border-white/40 bg-black/30";

  return (
    <button
      onClick={(e) => {
        e.stopPropagation();
        onClick(e);
      }}
      className={`absolute top-2 left-2 w-5 h-5 rounded border-2 flex items-center justify-center transition-all ${selectedClass} ${visibilityClass}`}
    >
      {isSelected && <Check className="w-3 h-3 text-white" />}
    </button>
  );
}
