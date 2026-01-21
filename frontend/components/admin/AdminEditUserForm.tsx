"use client";

import { useTranslations } from "next-intl";
import { Shield } from "lucide-react";
import type { AdminUserUpdate, UserRole } from "@/lib/api";
import { Spinner, Dialog } from "@/components/ui";

interface AdminEditUserFormProps {
  isOpen: boolean;
  onClose: () => void;
  editForm: AdminUserUpdate;
  onFormChange: (form: AdminUserUpdate) => void;
  onSave: () => void;
  isPending: boolean;
  error?: Error | null;
}

export function AdminEditUserForm({
  isOpen,
  onClose,
  editForm,
  onFormChange,
  onSave,
  isPending,
  error,
}: AdminEditUserFormProps): JSX.Element {
  const t = useTranslations("admin");

  return (
    <Dialog
      isOpen={isOpen}
      onClose={onClose}
      title={t("users.editUser")}
      icon={<Shield className="w-5 h-5 text-primary-400" />}
      maxWidth="sm"
    >
      <div className="space-y-4">
        <div>
          <label className="block text-sm font-medium text-text-secondary mb-2">
            {t("users.form.name")}
          </label>
          <input
            type="text"
            value={editForm.name || ""}
            onChange={(e) => onFormChange({ ...editForm, name: e.target.value })}
            className="input-field w-full"
          />
        </div>
        <div>
          <label className="block text-sm font-medium text-text-secondary mb-2">
            {t("users.form.role")}
          </label>
          <select
            value={editForm.role || ""}
            onChange={(e) => onFormChange({ ...editForm, role: e.target.value as UserRole })}
            className="input-field w-full"
          >
            <option value="viewer">Viewer</option>
            <option value="researcher">Researcher</option>
            <option value="admin">Admin</option>
          </select>
        </div>
        {error && (
          <p className="text-sm text-accent-red">{error.message}</p>
        )}
        <div className="flex justify-end gap-3 pt-4">
          <button onClick={onClose} className="btn-secondary">
            {t("common.cancel")}
          </button>
          <button
            onClick={onSave}
            disabled={isPending}
            className="btn-primary"
          >
            {isPending ? <Spinner size="sm" /> : t("common.save")}
          </button>
        </div>
      </div>
    </Dialog>
  );
}
