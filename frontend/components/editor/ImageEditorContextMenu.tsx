"use client";

/**
 * ImageEditorContextMenu Component
 *
 * Right-click context menu for bbox operations.
 */

import { useEffect, useRef } from "react";
import { useTranslations } from "next-intl";
import { motion, AnimatePresence } from "framer-motion";
import { Trash2, RotateCcw } from "lucide-react";
import type { EditorBbox, Point } from "@/lib/editor/types";

interface ImageEditorContextMenuProps {
  isOpen: boolean;
  position: Point | null;
  targetBbox: EditorBbox | null;
  onDelete: (bbox: EditorBbox) => void;
  onReset: (bbox: EditorBbox) => void;
  onClose: () => void;
}

export function ImageEditorContextMenu({
  isOpen,
  position,
  targetBbox,
  onDelete,
  onReset,
  onClose,
}: ImageEditorContextMenuProps) {
  const t = useTranslations("editor");
  const menuRef = useRef<HTMLDivElement>(null);

  // Close on click outside
  useEffect(() => {
    if (!isOpen) return;

    const handleClickOutside = (e: MouseEvent) => {
      if (menuRef.current && !menuRef.current.contains(e.target as Node)) {
        onClose();
      }
    };

    const handleEscape = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        onClose();
      }
    };

    document.addEventListener("mousedown", handleClickOutside);
    document.addEventListener("keydown", handleEscape);

    return () => {
      document.removeEventListener("mousedown", handleClickOutside);
      document.removeEventListener("keydown", handleEscape);
    };
  }, [isOpen, onClose]);

  if (!targetBbox) return null;

  return (
    <AnimatePresence>
      {isOpen && position && (
        <motion.div
          ref={menuRef}
          initial={{ opacity: 0, scale: 0.95 }}
          animate={{ opacity: 1, scale: 1 }}
          exit={{ opacity: 0, scale: 0.95 }}
          transition={{ duration: 0.1 }}
          style={{
            position: "fixed",
            left: position.x,
            top: position.y,
          }}
          className="z-[100] bg-bg-elevated border border-white/10 rounded-lg shadow-xl py-1 min-w-[160px]"
        >
          {/* Delete option */}
          <button
            onClick={() => {
              onDelete(targetBbox);
              onClose();
            }}
            className="w-full px-3 py-2 text-left text-sm text-accent-red hover:bg-accent-red/10 flex items-center gap-2 transition-colors"
          >
            <Trash2 className="w-4 h-4" />
            {t("deleteBbox")}
          </button>

          {/* Reset option (only for modified bboxes) */}
          {targetBbox.isModified && targetBbox.original && (
            <button
              onClick={() => {
                onReset(targetBbox);
                onClose();
              }}
              className="w-full px-3 py-2 text-left text-sm text-text-secondary hover:bg-white/5 flex items-center gap-2 transition-colors"
            >
              <RotateCcw className="w-4 h-4" />
              Reset to Original
            </button>
          )}
        </motion.div>
      )}
    </AnimatePresence>
  );
}
