"use client";

import { motion, AnimatePresence } from "framer-motion";
import { Check } from "lucide-react";
import { checkIconVariants, buttonHoverProps } from "@/lib/animations";

interface SelectionCheckboxProps {
  isSelected: boolean;
  onClick: (e: React.MouseEvent) => void;
  /** Whether the checkbox is always visible or only on hover */
  alwaysVisible?: boolean;
}

/**
 * A reusable selection checkbox for gallery items.
 * Positioned absolutely in the top-left corner of its container.
 * Features bouncy check animation and hover/tap feedback.
 */
export function SelectionCheckbox({
  isSelected,
  onClick,
  alwaysVisible = false,
}: SelectionCheckboxProps): JSX.Element {
  const visibilityClass = isSelected || alwaysVisible ? "" : "opacity-0 group-hover:opacity-100";

  return (
    <motion.button
      onClick={(e) => {
        e.stopPropagation();
        onClick(e);
      }}
      className={`absolute top-2 left-2 w-5 h-5 rounded border-2 flex items-center justify-center transition-colors ${visibilityClass}`}
      initial={false}
      animate={{
        backgroundColor: isSelected ? "rgb(0, 212, 170)" : "rgba(0, 0, 0, 0.3)",
        borderColor: isSelected ? "rgb(0, 212, 170)" : "rgba(255, 255, 255, 0.4)",
      }}
      whileHover={{ scale: 1.1 }}
      whileTap={{ scale: 0.9 }}
      transition={{ duration: 0.15 }}
    >
      <AnimatePresence>
        {isSelected && (
          <motion.div
            variants={checkIconVariants}
            initial="hidden"
            animate="visible"
            exit="hidden"
          >
            <Check className="w-3 h-3 text-white" strokeWidth={3} />
          </motion.div>
        )}
      </AnimatePresence>
    </motion.button>
  );
}
