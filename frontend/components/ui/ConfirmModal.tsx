"use client";

import { ReactNode } from "react";
import { motion, AnimatePresence } from "framer-motion";
import { X, AlertTriangle, Loader2 } from "lucide-react";

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
}: ConfirmModalProps): JSX.Element | null {
  const variantStyles = {
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
  };

  const styles = variantStyles[variant];

  return (
    <AnimatePresence>
      {isOpen && (
        <motion.div
          initial={{ opacity: 0 }}
          animate={{ opacity: 1 }}
          exit={{ opacity: 0 }}
          className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 backdrop-blur-sm p-4"
          onClick={onClose}
        >
          <motion.div
            initial={{ scale: 0.95, opacity: 0 }}
            animate={{ scale: 1, opacity: 1 }}
            exit={{ scale: 0.95, opacity: 0 }}
            onClick={(e) => e.stopPropagation()}
            className="glass-card p-6 w-full max-w-md"
          >
            <div className="flex items-start gap-4">
              <div className={`p-3 rounded-xl ${styles.iconBg}`}>
                {icon || <AlertTriangle className={`w-6 h-6 ${styles.iconColor}`} />}
              </div>
              <div className="flex-1">
                <div className="flex items-center justify-between">
                  <h3 className="text-lg font-display font-semibold text-text-primary">
                    {title}
                  </h3>
                  <button
                    onClick={onClose}
                    disabled={isLoading}
                    className="p-1 hover:bg-white/10 rounded-lg transition-colors"
                  >
                    <X className="w-5 h-5 text-text-muted" />
                  </button>
                </div>
                <p className="text-text-secondary mt-2">{message}</p>
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
    </AnimatePresence>
  );
}
