"use client";

/**
 * KeyboardShortcutsModal Component
 *
 * Displays all available keyboard shortcuts for the image editor.
 * Organized by category: Navigation, View, Editing.
 */

import { useTranslations } from "next-intl";
import { Keyboard } from "lucide-react";
import { Dialog } from "@/components/ui";

/** Visual keyboard key component */
function KeyCap({ children, className = "" }: { children: React.ReactNode; className?: string }) {
  return (
    <kbd className={`inline-flex items-center justify-center min-w-[24px] h-6 px-1.5 text-xs font-mono font-medium bg-white/10 border border-white/20 rounded text-text-primary shadow-sm ${className}`}>
      {children}
    </kbd>
  );
}

/** Single shortcut row */
function ShortcutRow({ keys, action }: { keys: React.ReactNode; action: string }) {
  return (
    <div className="flex items-center justify-between py-1.5">
      <span className="text-sm text-text-secondary">{action}</span>
      <div className="flex items-center gap-1">
        {keys}
      </div>
    </div>
  );
}

/** Category section */
function ShortcutSection({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div className="space-y-1">
      <h4 className="text-xs font-semibold text-text-muted uppercase tracking-wider mb-2">{title}</h4>
      <div className="space-y-0.5">
        {children}
      </div>
    </div>
  );
}

interface KeyboardShortcutsModalProps {
  isOpen: boolean;
  onClose: () => void;
}

export function KeyboardShortcutsModal({ isOpen, onClose }: KeyboardShortcutsModalProps) {
  const t = useTranslations("editor.shortcutsModal");

  return (
    <Dialog
      isOpen={isOpen}
      onClose={onClose}
      title={t("title")}
      icon={<Keyboard className="w-5 h-5 text-primary-400" />}
      maxWidth="md"
    >
      <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
        {/* Navigation */}
        <ShortcutSection title={t("navigation")}>
          <ShortcutRow
            keys={<KeyCap>←</KeyCap>}
            action={t("prevImage")}
          />
          <ShortcutRow
            keys={<KeyCap>→</KeyCap>}
            action={t("nextImage")}
          />
          <ShortcutRow
            keys={<KeyCap>Esc</KeyCap>}
            action={t("close")}
          />
        </ShortcutSection>

        {/* View */}
        <ShortcutSection title={t("view")}>
          <ShortcutRow
            keys={<KeyCap>↑</KeyCap>}
            action={t("zoomIn")}
          />
          <ShortcutRow
            keys={<KeyCap>↓</KeyCap>}
            action={t("zoomOut")}
          />
          <ShortcutRow
            keys={<KeyCap>F</KeyCap>}
            action={t("fitToView")}
          />
          <ShortcutRow
            keys={<><KeyCap>Space</KeyCap><span className="text-text-muted text-xs mx-1">+</span><span className="text-xs text-text-muted">drag</span></>}
            action={t("pan")}
          />
        </ShortcutSection>

        {/* Editing */}
        <ShortcutSection title={t("editing")}>
          <ShortcutRow
            keys={<><KeyCap>A</KeyCap><span className="text-text-muted text-xs mx-1">/</span><KeyCap>N</KeyCap></>}
            action={t("toggleAddMode")}
          />
          <ShortcutRow
            keys={<><KeyCap>D</KeyCap><span className="text-text-muted text-xs mx-1">/</span><KeyCap>Del</KeyCap></>}
            action={t("deleteCell")}
          />
          <ShortcutRow
            keys={<><span className="text-xs text-text-muted">↻ drag</span><span className="text-text-muted text-xs mx-1">+</span><KeyCap>Shift</KeyCap></>}
            action={t("rotateCell")}
          />
          <ShortcutRow
            keys={<><KeyCap>Z</KeyCap><span className="text-text-muted text-xs mx-1">/</span><KeyCap>Ctrl</KeyCap><span className="text-text-muted text-xs mx-0.5">+</span><KeyCap>Z</KeyCap></>}
            action={t("undo")}
          />
        </ShortcutSection>
      </div>

      {/* Help tip */}
      <div className="mt-6 pt-4 border-t border-white/10">
        <p className="text-xs text-text-muted text-center">
          {t("helpTip")}
        </p>
      </div>
    </Dialog>
  );
}
