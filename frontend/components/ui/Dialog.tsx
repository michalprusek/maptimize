"use client";

import { ReactNode } from "react";
import { motion, AnimatePresence } from "framer-motion";
import { X } from "lucide-react";

interface DialogProps {
  isOpen: boolean;
  onClose: () => void;
  title: string;
  icon?: ReactNode;
  children: ReactNode;
  maxWidth?: "sm" | "md" | "lg";
}

const maxWidthClasses = {
  sm: "max-w-sm",
  md: "max-w-md",
  lg: "max-w-lg",
};

export function Dialog({
  isOpen,
  onClose,
  title,
  icon,
  children,
  maxWidth = "md",
}: DialogProps): JSX.Element | null {
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
            className={`glass-card p-6 w-full ${maxWidthClasses[maxWidth]}`}
          >
            <div className="flex items-center justify-between mb-6">
              <div className="flex items-center gap-3">
                {icon && (
                  <div className="p-2 bg-primary-500/20 rounded-lg">
                    {icon}
                  </div>
                )}
                <h3 className="text-lg font-display font-semibold text-text-primary">
                  {title}
                </h3>
              </div>
              <button
                onClick={onClose}
                className="p-1 hover:bg-white/10 rounded-lg transition-colors"
              >
                <X className="w-5 h-5 text-text-muted" />
              </button>
            </div>
            {children}
          </motion.div>
        </motion.div>
      )}
    </AnimatePresence>
  );
}

interface ConfirmDialogProps {
  isOpen: boolean;
  onClose: () => void;
  onConfirm: () => void;
  title: string;
  message: string;
  detail?: string;
  confirmLabel?: string;
  cancelLabel?: string;
  isLoading?: boolean;
  variant?: "danger" | "primary";
  icon?: ReactNode;
}

export function ConfirmDialog({
  isOpen,
  onClose,
  onConfirm,
  title,
  message,
  detail,
  confirmLabel = "Confirm",
  cancelLabel = "Cancel",
  isLoading = false,
  variant = "primary",
  icon,
}: ConfirmDialogProps): JSX.Element | null {
  const buttonClass = variant === "danger"
    ? "btn-primary bg-accent-red hover:bg-accent-red/80"
    : "btn-primary";

  return (
    <Dialog isOpen={isOpen} onClose={onClose} title={title} icon={icon}>
      <p className="text-text-secondary mb-2">{message}</p>
      {detail && (
        <p className="text-sm text-text-muted mb-6 font-mono bg-bg-secondary px-3 py-2 rounded">
          {detail}
        </p>
      )}
      <div className="flex gap-3 justify-end">
        <button
          onClick={onClose}
          disabled={isLoading}
          className="px-4 py-2 text-text-secondary hover:text-text-primary transition-colors"
        >
          {cancelLabel}
        </button>
        <button
          onClick={onConfirm}
          disabled={isLoading}
          className={`${buttonClass} flex items-center gap-2`}
        >
          {isLoading ? (
            <>
              <span className="w-4 h-4 border-2 border-current border-t-transparent rounded-full animate-spin" />
              Loading...
            </>
          ) : (
            confirmLabel
          )}
        </button>
      </div>
    </Dialog>
  );
}
