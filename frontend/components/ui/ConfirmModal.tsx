"use client";

import type { ReactNode } from "react";
import { useEffect, useState, useCallback } from "react";
import { createPortal } from "react-dom";
import { motion, AnimatePresence } from "framer-motion";
import { X, AlertTriangle, Loader2 } from "lucide-react";
import { modalOverlayAnimation, modalContentAnimation } from "@/lib/animations";

interface ConfirmModalProps {
  isOpen: boolean;
  onClose: () => void;
  onConfirm: () => void;
  title: string;
  message: string;
  detail?: string;
  confirmLabel?: string;
  cancelLabel?: string;
  isLoading?: boolean;
  variant?: "danger" | "warning" | "primary";
  icon?: ReactNode;
}

const VARIANT_STYLES = {
  danger: {
    button: "bg-accent-red hover:bg-accent-red/80",
    iconBg: "bg-accent-red/20",
    iconColor: "text-accent-red",
  },
  warning: {
    button: "bg-accent-amber hover:bg-accent-amber/80",
    iconBg: "bg-accent-amber/20",
    iconColor: "text-accent-amber",
  },
  primary: {
    button: "bg-primary-500 hover:bg-primary-600",
    iconBg: "bg-primary-500/20",
    iconColor: "text-primary-400",
  },
} as const;

export function ConfirmModal({
  isOpen,
  onClose,
  onConfirm,
  title,
  message,
  detail,
  confirmLabel = "Confirm",
  cancelLabel = "Cancel",
  isLoading = false,
  variant = "danger",
  icon,
}: ConfirmModalProps): ReactNode {
  const [mounted, setMounted] = useState(false);
  const styles = VARIANT_STYLES[variant];

  useEffect(() => {
    setMounted(true);
  }, []);

  // Keyboard handling: Escape to close, Enter to confirm
  const handleKeyDown = useCallback(
    (event: KeyboardEvent) => {
      if (event.key === "Escape" && !isLoading) {
        onClose();
      }
      if (event.key === "Enter" && !isLoading) {
        onConfirm();
      }
    },
    [onClose, onConfirm, isLoading]
  );

  useEffect(() => {
    if (isOpen) {
      document.addEventListener("keydown", handleKeyDown);
      return () => document.removeEventListener("keydown", handleKeyDown);
    }
  }, [isOpen, handleKeyDown]);

  if (!mounted) return null;

  return createPortal(
    <AnimatePresence>
      {isOpen && (
        <motion.div
          {...modalOverlayAnimation}
          className="fixed inset-0 z-[200] flex items-center justify-center bg-black/50 backdrop-blur-sm p-4"
          onClick={onClose}
        >
          <motion.div
            {...modalContentAnimation}
            onClick={(e) => e.stopPropagation()}
            className="glass-card p-6 w-full max-w-md"
            role="alertdialog"
            aria-modal="true"
            aria-labelledby="confirm-modal-title"
            aria-describedby="confirm-modal-message"
          >
            <div className="flex items-start gap-4">
              <div className={`p-3 rounded-xl ${styles.iconBg}`}>
                {icon || <AlertTriangle className={`w-6 h-6 ${styles.iconColor}`} />}
              </div>
              <div className="flex-1">
                <div className="flex items-center justify-between">
                  <h3
                    id="confirm-modal-title"
                    className="text-lg font-display font-semibold text-text-primary"
                  >
                    {title}
                  </h3>
                  <button
                    onClick={onClose}
                    disabled={isLoading}
                    className="p-1 hover:bg-white/10 rounded-lg transition-colors disabled:opacity-50"
                    aria-label="Close dialog"
                  >
                    <X className="w-5 h-5 text-text-muted" />
                  </button>
                </div>
                <p id="confirm-modal-message" className="text-text-secondary mt-2">
                  {message}
                </p>
                {detail && (
                  <p className="text-sm text-text-muted mt-2 font-mono bg-bg-secondary px-3 py-2 rounded">
                    {detail}
                  </p>
                )}
              </div>
            </div>

            <div className="flex gap-3 justify-end mt-6 pt-4 border-t border-white/5">
              <button
                onClick={onClose}
                disabled={isLoading}
                className="px-4 py-2 text-text-secondary hover:text-text-primary transition-colors disabled:opacity-50"
              >
                {cancelLabel}
              </button>
              <button
                onClick={onConfirm}
                disabled={isLoading}
                className={`px-4 py-2 rounded-lg text-white font-medium flex items-center gap-2 transition-colors disabled:opacity-50 ${styles.button}`}
              >
                {isLoading ? (
                  <>
                    <Loader2 className="w-4 h-4 animate-spin" />
                    Loading...
                  </>
                ) : (
                  confirmLabel
                )}
              </button>
            </div>
          </motion.div>
        </motion.div>
      )}
    </AnimatePresence>,
    document.body
  );
}
