"use client";

import { Trash2 } from "lucide-react";

interface DeleteOverlayButtonProps {
  onClick: (e: React.MouseEvent) => void;
  title?: string;
}

/**
 * A reusable delete button for gallery items.
 * Positioned absolutely in the top-right corner, visible on hover.
 */
export function DeleteOverlayButton({
  onClick,
  title = "Delete",
}: DeleteOverlayButtonProps): JSX.Element {
  return (
    <button
      onClick={(e) => onClick(e)}
      className="absolute top-2 right-2 p-1.5 bg-bg-primary/80 hover:bg-accent-red/20 text-text-muted hover:text-accent-red rounded-lg opacity-0 group-hover:opacity-100 transition-all duration-200"
      title={title}
    >
      <Trash2 className="w-4 h-4" />
    </button>
  );
}
