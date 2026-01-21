"use client";

import { useState } from "react";
import { useParams, useRouter } from "next/navigation";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { useTranslations } from "next-intl";
import { motion } from "framer-motion";
import {
  ArrowLeft,
  Mail,
  Calendar,
  Clock,
  Shield,
  Microscope,
  Image as ImageIcon,
  FileText,
  MessageSquare,
  HardDrive,
  Edit,
  Trash2,
  Key,
  Copy,
  Check,
} from "lucide-react";
import { api } from "@/lib/api";
import type { AdminUserUpdate } from "@/lib/api";
import { Spinner, ConfirmModal, Dialog } from "@/components/ui";
import { formatBytes, formatDate, formatDateTime } from "@/lib/utils";
import {
  AdminStorageChart,
  AdminConversationViewer,
  AdminEditUserForm,
  AdminLoadingState,
  AdminErrorState,
  AdminStatusBadge,
  roleColors,
} from "@/components/admin";

type TabType = "conversations" | "experiments" | "storage";

export default function AdminUserDetailPage() {
  const params = useParams();
  const router = useRouter();
  const queryClient = useQueryClient();
  const t = useTranslations("admin");

  const userId = Number(params.userId);

  // State
  const [activeTab, setActiveTab] = useState<TabType>("conversations");
  const [isEditOpen, setIsEditOpen] = useState(false);
  const [isDeleteOpen, setIsDeleteOpen] = useState(false);
  const [isResetPasswordOpen, setIsResetPasswordOpen] = useState(false);
  const [newPassword, setNewPassword] = useState<string | null>(null);
  const [copiedPassword, setCopiedPassword] = useState(false);
  const [editForm, setEditForm] = useState<AdminUserUpdate>({});

  // Fetch user detail
  const { data: user, isLoading, isError, refetch } = useQuery({
    queryKey: ["admin", "user", userId],
    queryFn: () => api.getAdminUserDetail(userId),
  });

  // Fetch experiments
  const { data: experimentsData, isLoading: experimentsLoading, isError: experimentsError, refetch: refetchExperiments } = useQuery({
    queryKey: ["admin", "user", userId, "experiments"],
    queryFn: () => api.getAdminUserExperiments(userId),
    enabled: activeTab === "experiments",
  });

  // Mutations
  const updateMutation = useMutation({
    mutationFn: (data: AdminUserUpdate) => api.updateAdminUser(userId, data),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["admin", "user", userId] });
      queryClient.invalidateQueries({ queryKey: ["admin", "users"] });
      setIsEditOpen(false);
    },
  });

  const deleteMutation = useMutation({
    mutationFn: () => api.deleteAdminUser(userId),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["admin", "users"] });
      queryClient.invalidateQueries({ queryKey: ["admin", "stats"] });
      router.push("/admin/users");
    },
  });

  const resetPasswordMutation = useMutation({
    mutationFn: () => api.resetAdminUserPassword(userId),
    onSuccess: (data) => {
      setNewPassword(data.new_password);
    },
  });

  const handleEdit = () => {
    if (user) {
      setEditForm({ name: user.name, role: user.role });
      setIsEditOpen(true);
    }
  };

  const handleSaveEdit = () => {
    updateMutation.mutate(editForm);
  };

  const handleResetPassword = () => {
    setIsResetPasswordOpen(true);
    setNewPassword(null);
    setCopiedPassword(false);
  };

  const handleConfirmResetPassword = () => {
    resetPasswordMutation.mutate();
  };

  const handleCopyPassword = async () => {
    if (newPassword) {
      try {
        await navigator.clipboard.writeText(newPassword);
        setCopiedPassword(true);
        setTimeout(() => setCopiedPassword(false), 2000);
      } catch {
        // Fallback for browsers that don't support clipboard API or when permission is denied
        const textArea = document.createElement("textarea");
        textArea.value = newPassword;
        textArea.style.position = "fixed";
        textArea.style.left = "-999999px";
        document.body.appendChild(textArea);
        textArea.select();
        try {
          document.execCommand("copy");
          setCopiedPassword(true);
          setTimeout(() => setCopiedPassword(false), 2000);
        } catch {
          // Show error to user - they need to copy manually
          alert(t("common.copyFailed") || "Failed to copy. Please copy manually.");
        }
        document.body.removeChild(textArea);
      }
    }
  };

  if (isLoading) {
    return <AdminLoadingState />;
  }

  if (isError) {
    return <AdminErrorState message={t("userDetail.loadError")} onRetry={() => refetch()} />;
  }

  if (!user) {
    return (
      <div className="text-center py-12">
        <p className="text-text-muted">{t("userDetail.userNotFound")}</p>
      </div>
    );
  }

  return (
    <motion.div
      initial={{ opacity: 0, y: 20 }}
      animate={{ opacity: 1, y: 0 }}
      className="space-y-6"
    >
      {/* Header */}
      <div className="flex items-center gap-4">
        <button
          onClick={() => router.push("/admin/users")}
          className="p-2 rounded-lg hover:bg-white/5 text-text-secondary hover:text-text-primary transition-colors"
        >
          <ArrowLeft className="w-5 h-5" />
        </button>
        <div className="flex-1">
          <h1 className="text-2xl font-bold text-text-primary">{t("userDetail.title")}</h1>
        </div>
      </div>

      {/* User Info Card */}
      <div className="glass-card p-6">
        <div className="flex flex-col md:flex-row md:items-start gap-6">
          {/* Avatar and basic info */}
          <div className="flex items-center gap-4">
            <div className="w-20 h-20 rounded-full bg-primary-500/20 flex items-center justify-center text-primary-400 text-3xl font-bold">
              {user.name.charAt(0).toUpperCase()}
            </div>
            <div>
              <h2 className="text-xl font-semibold text-text-primary">{user.name}</h2>
              <p className="text-text-secondary flex items-center gap-2">
                <Mail className="w-4 h-4" />
                {user.email}
              </p>
              <span className={`inline-flex items-center gap-1.5 px-2.5 py-1 mt-2 rounded-full text-xs font-medium border ${roleColors[user.role]}`}>
                <Shield className="w-3 h-3" />
                {user.role}
              </span>
            </div>
          </div>

          {/* Actions */}
          <div className="md:ml-auto flex flex-wrap gap-2">
            <button
              onClick={handleEdit}
              className="btn-secondary flex items-center gap-2"
            >
              <Edit className="w-4 h-4" />
              {t("userDetail.edit")}
            </button>
            <button
              onClick={handleResetPassword}
              className="btn-secondary flex items-center gap-2"
            >
              <Key className="w-4 h-4" />
              {t("userDetail.resetPassword")}
            </button>
            <button
              onClick={() => setIsDeleteOpen(true)}
              className="btn-danger flex items-center gap-2"
            >
              <Trash2 className="w-4 h-4" />
              {t("userDetail.delete")}
            </button>
          </div>
        </div>

        {/* Stats Grid */}
        <div className="grid grid-cols-2 md:grid-cols-4 gap-4 mt-6 pt-6 border-t border-white/10">
          <div className="flex items-center gap-3">
            <div className="p-2 rounded-lg bg-green-500/10">
              <Microscope className="w-5 h-5 text-green-400" />
            </div>
            <div>
              <p className="text-2xl font-bold text-text-primary">{user.experiment_count}</p>
              <p className="text-xs text-text-muted">{t("userDetail.experiments")}</p>
            </div>
          </div>
          <div className="flex items-center gap-3">
            <div className="p-2 rounded-lg bg-blue-500/10">
              <ImageIcon className="w-5 h-5 text-blue-400" />
            </div>
            <div>
              <p className="text-2xl font-bold text-text-primary">{user.image_count}</p>
              <p className="text-xs text-text-muted">{t("userDetail.images")}</p>
            </div>
          </div>
          <div className="flex items-center gap-3">
            <div className="p-2 rounded-lg bg-purple-500/10">
              <FileText className="w-5 h-5 text-purple-400" />
            </div>
            <div>
              <p className="text-2xl font-bold text-text-primary">{user.document_count}</p>
              <p className="text-xs text-text-muted">{t("userDetail.documents")}</p>
            </div>
          </div>
          <div className="flex items-center gap-3">
            <div className="p-2 rounded-lg bg-amber-500/10">
              <MessageSquare className="w-5 h-5 text-amber-400" />
            </div>
            <div>
              <p className="text-2xl font-bold text-text-primary">{user.chat_thread_count}</p>
              <p className="text-xs text-text-muted">{t("userDetail.conversations")}</p>
            </div>
          </div>
        </div>

        {/* Dates */}
        <div className="flex flex-wrap gap-6 mt-6 pt-6 border-t border-white/10 text-sm text-text-secondary">
          <div className="flex items-center gap-2">
            <Calendar className="w-4 h-4" />
            <span>{t("userDetail.registered")}: {formatDateTime(user.created_at)}</span>
          </div>
          <div className="flex items-center gap-2">
            <Clock className="w-4 h-4" />
            <span>{t("userDetail.lastLogin")}: {formatDateTime(user.last_login)}</span>
          </div>
        </div>
      </div>

      {/* Tabs */}
      <div className="flex gap-2 border-b border-white/10">
        {(["conversations", "experiments", "storage"] as TabType[]).map((tab) => (
          <button
            key={tab}
            onClick={() => setActiveTab(tab)}
            className={`px-4 py-2 text-sm font-medium border-b-2 -mb-px transition-colors ${
              activeTab === tab
                ? "border-primary-500 text-primary-400"
                : "border-transparent text-text-secondary hover:text-text-primary"
            }`}
          >
            {t(`userDetail.tabs.${tab}`)}
          </button>
        ))}
      </div>

      {/* Tab Content */}
      <div className="min-h-[400px]">
        {activeTab === "conversations" && (
          <AdminConversationViewer userId={userId} />
        )}

        {activeTab === "experiments" && (
          <div className="glass-card">
            {experimentsLoading ? (
              <AdminLoadingState height="py-12" />
            ) : experimentsError ? (
              <AdminErrorState
                height="py-12"
                iconSize="md"
                message={t("userDetail.loadError")}
                onRetry={() => refetchExperiments()}
              />
            ) : experimentsData?.experiments.length === 0 ? (
              <div className="text-center py-12 text-text-muted">
                <Microscope className="w-12 h-12 mx-auto mb-3 opacity-50" />
                <p>{t("userDetail.noExperiments")}</p>
              </div>
            ) : (
              <div className="overflow-x-auto">
                <table className="w-full">
                  <thead>
                    <tr className="border-b border-white/10">
                      <th className="px-4 py-3 text-left text-xs font-medium text-text-secondary uppercase">
                        {t("userDetail.experimentName")}
                      </th>
                      <th className="px-4 py-3 text-left text-xs font-medium text-text-secondary uppercase">
                        {t("userDetail.status")}
                      </th>
                      <th className="px-4 py-3 text-right text-xs font-medium text-text-secondary uppercase">
                        {t("userDetail.images")}
                      </th>
                      <th className="px-4 py-3 text-left text-xs font-medium text-text-secondary uppercase">
                        {t("userDetail.created")}
                      </th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-white/5">
                    {experimentsData?.experiments.map((exp) => (
                      <tr key={exp.id} className="hover:bg-white/5">
                        <td className="px-4 py-3">
                          <p className="text-sm font-medium text-text-primary">{exp.name}</p>
                          {exp.description && (
                            <p className="text-xs text-text-muted truncate max-w-xs">
                              {exp.description}
                            </p>
                          )}
                        </td>
                        <td className="px-4 py-3">
                          <AdminStatusBadge status={exp.status} />
                        </td>
                        <td className="px-4 py-3 text-sm text-text-secondary text-right">
                          {exp.image_count}
                        </td>
                        <td className="px-4 py-3 text-sm text-text-secondary">
                          {formatDate(exp.created_at)}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </div>
        )}

        {activeTab === "storage" && (
          <div className="glass-card p-6">
            <h3 className="text-lg font-semibold text-text-primary mb-6 flex items-center gap-2">
              <HardDrive className="w-5 h-5 text-amber-400" />
              {t("userDetail.storageBreakdown")}
            </h3>
            <div className="max-w-lg mx-auto">
              <AdminStorageChart
                data={{
                  images_storage_bytes: user.images_storage_bytes,
                  documents_storage_bytes: user.documents_storage_bytes,
                }}
                height={250}
              />
            </div>
            <div className="mt-6 pt-6 border-t border-white/10">
              <div className="grid grid-cols-1 md:grid-cols-3 gap-4 text-center">
                <div>
                  <p className="text-2xl font-bold text-text-primary">
                    {formatBytes(user.total_storage_bytes)}
                  </p>
                  <p className="text-sm text-text-muted">{t("userDetail.totalStorage")}</p>
                </div>
                <div>
                  <p className="text-2xl font-bold text-blue-400">
                    {formatBytes(user.images_storage_bytes)}
                  </p>
                  <p className="text-sm text-text-muted">{t("userDetail.imagesStorage")}</p>
                </div>
                <div>
                  <p className="text-2xl font-bold text-purple-400">
                    {formatBytes(user.documents_storage_bytes)}
                  </p>
                  <p className="text-sm text-text-muted">{t("userDetail.documentsStorage")}</p>
                </div>
              </div>
            </div>
          </div>
        )}
      </div>

      {/* Edit Dialog */}
      <AdminEditUserForm
        isOpen={isEditOpen}
        onClose={() => setIsEditOpen(false)}
        editForm={editForm}
        onFormChange={setEditForm}
        onSave={handleSaveEdit}
        isPending={updateMutation.isPending}
        error={updateMutation.error}
      />

      {/* Reset Password Dialog */}
      <Dialog
        isOpen={isResetPasswordOpen}
        onClose={() => {
          setIsResetPasswordOpen(false);
          setNewPassword(null);
        }}
        title={t("userDetail.resetPassword")}
        icon={<Key className="w-5 h-5 text-amber-400" />}
        maxWidth="sm"
      >
        {!newPassword ? (
          <div className="space-y-4">
            <p className="text-text-secondary">
              {t("userDetail.resetPasswordConfirmation", { email: user.email })}
            </p>
            {resetPasswordMutation.error && (
              <p className="text-sm text-accent-red">{resetPasswordMutation.error.message}</p>
            )}
            <div className="flex justify-end gap-3 pt-4">
              <button
                onClick={() => setIsResetPasswordOpen(false)}
                className="btn-secondary"
              >
                {t("common.cancel")}
              </button>
              <button
                onClick={handleConfirmResetPassword}
                disabled={resetPasswordMutation.isPending}
                className="btn-primary bg-amber-500 hover:bg-amber-600"
              >
                {resetPasswordMutation.isPending ? <Spinner size="sm" /> : t("userDetail.resetPassword")}
              </button>
            </div>
          </div>
        ) : (
          <div className="space-y-4">
            <p className="text-text-secondary">{t("userDetail.newPasswordGenerated")}</p>
            <div className="flex items-center gap-2 bg-bg-primary p-3 rounded-lg">
              <code className="flex-1 font-mono text-lg text-primary-400">
                {newPassword}
              </code>
              <button
                onClick={handleCopyPassword}
                className="p-2 rounded-lg hover:bg-white/10 transition-colors"
              >
                {copiedPassword ? (
                  <Check className="w-5 h-5 text-green-400" />
                ) : (
                  <Copy className="w-5 h-5 text-text-secondary" />
                )}
              </button>
            </div>
            <p className="text-xs text-text-muted">
              {t("userDetail.copyPasswordWarning")}
            </p>
            <div className="flex justify-end pt-4">
              <button
                onClick={() => {
                  setIsResetPasswordOpen(false);
                  setNewPassword(null);
                }}
                className="btn-primary"
              >
                {t("common.done")}
              </button>
            </div>
          </div>
        )}
      </Dialog>

      {/* Delete Confirmation */}
      <ConfirmModal
        isOpen={isDeleteOpen}
        onClose={() => setIsDeleteOpen(false)}
        onConfirm={() => deleteMutation.mutate()}
        title={t("users.deleteUser")}
        message={t("userDetail.deleteWarning")}
        detail={user.email}
        variant="danger"
        confirmLabel={t("common.delete")}
        cancelLabel={t("common.cancel")}
        isLoading={deleteMutation.isPending}
      />
    </motion.div>
  );
}
