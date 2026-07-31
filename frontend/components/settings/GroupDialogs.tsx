"use client";

/**
 * The create/edit dialogs for a group. Split out of the settings page with the
 * groups section: the page had grown past 1200 lines and the group UI was a
 * third of it.
 */
import { useState } from "react";
import { motion } from "framer-motion";
import { Loader2, X } from "lucide-react";
import { useTranslations } from "next-intl";

import type { GroupDetail } from "@/lib/api";

// =============================================================================
// Create Group Dialog
// =============================================================================

export function CreateGroupDialog({
  onClose,
  onSubmit,
  isPending,
}: {
  onClose: () => void;
  onSubmit: (data: { name: string; description?: string }) => void;
  isPending: boolean;
}): JSX.Element {
  const tg = useTranslations("groups");
  const tCommon = useTranslations("common");
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    onSubmit({ name, description: description || undefined });
  };

  return (
    <motion.div
      initial={{ opacity: 0 }}
      animate={{ opacity: 1 }}
      exit={{ opacity: 0 }}
      className="fixed inset-0 bg-black/50 backdrop-blur-sm flex items-center justify-center z-[100] p-4"
      onClick={onClose}
    >
      <motion.div
        initial={{ scale: 0.95, opacity: 0 }}
        animate={{ scale: 1, opacity: 1 }}
        exit={{ scale: 0.95, opacity: 0 }}
        className="glass-card p-6 w-full max-w-md"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-center justify-between mb-6">
          <h3 className="text-lg font-display font-semibold text-text-primary">{tg("createGroup")}</h3>
          <button onClick={onClose} className="p-2 hover:bg-white/5 rounded-lg transition-colors">
            <X className="w-5 h-5 text-text-muted" />
          </button>
        </div>

        <form onSubmit={handleSubmit} className="space-y-4">
          <div>
            <label className="block text-sm font-medium text-text-secondary mb-2">{tg("groupName")}</label>
            <input
              type="text"
              value={name}
              onChange={(e) => setName(e.target.value)}
              className="input-field"
              required
              autoFocus
            />
          </div>

          <div>
            <label className="block text-sm font-medium text-text-secondary mb-2">{tg("groupDescription")}</label>
            <textarea
              value={description}
              onChange={(e) => setDescription(e.target.value)}
              className="input-field min-h-[80px] resize-none"
            />
          </div>

          <div className="flex gap-3 pt-4">
            <button type="button" onClick={onClose} className="btn-secondary flex-1">
              {tCommon("cancel")}
            </button>
            <button
              type="submit"
              disabled={isPending || !name.trim()}
              className="btn-primary flex-1 flex items-center justify-center gap-2"
            >
              {isPending ? (
                <>
                  <Loader2 className="w-4 h-4 animate-spin" />
                  {tg("creating")}
                </>
              ) : (
                tg("createGroup")
              )}
            </button>
          </div>
        </form>
      </motion.div>
    </motion.div>
  );
}

// =============================================================================
// Edit Group Dialog
// =============================================================================

export function EditGroupDialog({
  group,
  onClose,
  onSubmit,
  isPending,
}: {
  group: GroupDetail;
  onClose: () => void;
  onSubmit: (data: { name?: string; description?: string }) => void;
  isPending: boolean;
}): JSX.Element {
  const tg = useTranslations("groups");
  const tCommon = useTranslations("common");
  const [name, setName] = useState(group.name);
  const [description, setDescription] = useState(group.description || "");

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    const updates: { name?: string; description?: string } = {};
    if (name !== group.name) updates.name = name;
    if (description !== (group.description || "")) updates.description = description;
    if (Object.keys(updates).length > 0) {
      onSubmit(updates);
    }
  };

  return (
    <motion.div
      initial={{ opacity: 0 }}
      animate={{ opacity: 1 }}
      exit={{ opacity: 0 }}
      className="fixed inset-0 bg-black/50 backdrop-blur-sm flex items-center justify-center z-[100] p-4"
      onClick={onClose}
    >
      <motion.div
        initial={{ scale: 0.95, opacity: 0 }}
        animate={{ scale: 1, opacity: 1 }}
        exit={{ scale: 0.95, opacity: 0 }}
        className="glass-card p-6 w-full max-w-md"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-center justify-between mb-6">
          <h3 className="text-lg font-display font-semibold text-text-primary">{tg("editGroup")}</h3>
          <button onClick={onClose} className="p-2 hover:bg-white/5 rounded-lg transition-colors">
            <X className="w-5 h-5 text-text-muted" />
          </button>
        </div>

        <form onSubmit={handleSubmit} className="space-y-4">
          <div>
            <label className="block text-sm font-medium text-text-secondary mb-2">{tg("groupName")}</label>
            <input
              type="text"
              value={name}
              onChange={(e) => setName(e.target.value)}
              className="input-field"
              required
              autoFocus
            />
          </div>

          <div>
            <label className="block text-sm font-medium text-text-secondary mb-2">{tg("groupDescription")}</label>
            <textarea
              value={description}
              onChange={(e) => setDescription(e.target.value)}
              className="input-field min-h-[80px] resize-none"
            />
          </div>

          <div className="flex gap-3 pt-4">
            <button type="button" onClick={onClose} className="btn-secondary flex-1">
              {tCommon("cancel")}
            </button>
            <button
              type="submit"
              disabled={isPending || !name.trim()}
              className="btn-primary flex-1 flex items-center justify-center gap-2"
            >
              {isPending ? (
                <>
                  <Loader2 className="w-4 h-4 animate-spin" />
                  {tg("saving")}
                </>
              ) : (
                tCommon("save")
              )}
            </button>
          </div>
        </form>
      </motion.div>
    </motion.div>
  );
}
