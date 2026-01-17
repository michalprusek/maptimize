"use client";

import { motion } from "framer-motion";
import { Trash2 } from "lucide-react";
import { iconButtonHoverProps } from "@/lib/animations";

interface DeleteOverlayButtonProps {
  onClick: (e: React.MouseEvent) => void;
  title?: string;
}

/**
 * A reusable delete button for gallery items.
 * Positioned absolutely in the top-right corner, visible on hover.
 * Features scale animation on hover/tap.
 */
export function DeleteOverlayButton({
  onClick,
  title = "Delete",
}: DeleteOverlayButtonProps): JSX.Element {
  return (
    <motion.button
      onClick={(e) => onClick(e)}
      className="absolute top-2 right-2 p-1.5 bg-bg-primary/80 hover:bg-accent-red/20 text-text-muted hover:text-accent-red rounded-lg opacity-0 group-hover:opacity-100 transition-colors duration-200"
      title={title}
      {...iconButtonHoverProps}
    >
      <Trash2 className="w-4 h-4" />
    </motion.button>
  );
}
