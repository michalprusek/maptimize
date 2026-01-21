"use client";

import { useState } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { useTranslations } from "next-intl";
import { motion } from "framer-motion";
import { Users, ArrowLeft } from "lucide-react";
import { useRouter } from "next/navigation";
import { api } from "@/lib/api";
import type { AdminUserListItem, AdminUserUpdate, UserRole } from "@/lib/api";
import { ConfirmModal } from "@/components/ui";
import {
  AdminUserTable,
  AdminEditUserForm,
  AdminLoadingState,
  AdminErrorState,
} from "@/components/admin";

export default function AdminUsersPage() {
  const t = useTranslations("admin");
  const router = useRouter();
  const queryClient = useQueryClient();

  // State for filters and pagination
  const [page, setPage] = useState(1);
  const [search, setSearch] = useState("");
  const [roleFilter, setRoleFilter] = useState<UserRole | undefined>();
  const [sortBy, setSortBy] = useState<"created_at" | "last_login" | "name" | "email">("created_at");
  const [sortOrder, setSortOrder] = useState<"asc" | "desc">("desc");

  // State for modals
  const [editingUser, setEditingUser] = useState<AdminUserListItem | null>(null);
  const [deletingUser, setDeletingUser] = useState<AdminUserListItem | null>(null);
  const [editForm, setEditForm] = useState<AdminUserUpdate>({});

  // Fetch users
  const { data, isLoading, error, refetch } = useQuery({
    queryKey: ["admin", "users", page, search, roleFilter, sortBy, sortOrder],
    queryFn: () =>
      api.getAdminUsers({
        page,
        page_size: 20,
        search: search || undefined,
        role: roleFilter,
        sort_by: sortBy,
        sort_order: sortOrder,
      }),
  });

  // Update user mutation
  const updateMutation = useMutation({
    mutationFn: ({ userId, data }: { userId: number; data: AdminUserUpdate }) =>
      api.updateAdminUser(userId, data),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["admin", "users"] });
      setEditingUser(null);
      setEditForm({});
    },
    onError: (error, variables) => {
      console.error(`[Admin] Failed to update user ${variables.userId}:`, error);
    },
  });

  // Delete user mutation
  const deleteMutation = useMutation({
    mutationFn: (userId: number) => api.deleteAdminUser(userId),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["admin", "users"] });
      queryClient.invalidateQueries({ queryKey: ["admin", "stats"] });
      setDeletingUser(null);
    },
    onError: (error, userId) => {
      console.error(`[Admin] Failed to delete user ${userId}:`, error);
    },
  });

  const handleEditUser = (user: AdminUserListItem) => {
    setEditingUser(user);
    setEditForm({ name: user.name, role: user.role });
  };

  const handleSaveEdit = () => {
    if (editingUser) {
      updateMutation.mutate({ userId: editingUser.id, data: editForm });
    }
  };

  const handleDeleteUser = (user: AdminUserListItem) => {
    setDeletingUser(user);
  };

  const handleConfirmDelete = () => {
    if (deletingUser) {
      deleteMutation.mutate(deletingUser.id);
    }
  };

  const handleSort = (newSortBy: string, newSortOrder: "asc" | "desc") => {
    setSortBy(newSortBy as typeof sortBy);
    setSortOrder(newSortOrder);
    setPage(1);
  };

  const handleSearch = (newSearch: string) => {
    setSearch(newSearch);
    setPage(1);
  };

  const handleRoleFilter = (role: UserRole | undefined) => {
    setRoleFilter(role);
    setPage(1);
  };

  return (
    <motion.div
      initial={{ opacity: 0, y: 20 }}
      animate={{ opacity: 1, y: 0 }}
      className="space-y-6"
    >
      {/* Header */}
      <div className="flex items-center gap-4">
        <button
          onClick={() => router.push("/admin")}
          className="p-2 rounded-lg hover:bg-white/5 text-text-secondary hover:text-text-primary transition-colors"
        >
          <ArrowLeft className="w-5 h-5" />
        </button>
        <div>
          <h1 className="text-2xl font-bold text-text-primary flex items-center gap-3">
            <Users className="w-7 h-7 text-primary-400" />
            {t("users.title")}
          </h1>
          <p className="text-text-secondary mt-1">{t("users.subtitle")}</p>
        </div>
      </div>

      {/* User Table */}
      {isLoading ? (
        <AdminLoadingState height="py-12" />
      ) : error ? (
        <AdminErrorState height="py-12" message={error.message} onRetry={() => refetch()} />
      ) : data ? (
        <AdminUserTable
          data={data}
          onPageChange={setPage}
          onSearch={handleSearch}
          onSort={handleSort}
          onRoleFilter={handleRoleFilter}
          onEditUser={handleEditUser}
          onDeleteUser={handleDeleteUser}
          sortBy={sortBy}
          sortOrder={sortOrder}
          searchQuery={search}
          roleFilter={roleFilter}
          isDeleting={deleteMutation.isPending}
        />
      ) : null}

      {/* Edit User Dialog */}
      <AdminEditUserForm
        isOpen={!!editingUser}
        onClose={() => setEditingUser(null)}
        editForm={editForm}
        onFormChange={setEditForm}
        onSave={handleSaveEdit}
        isPending={updateMutation.isPending}
        error={updateMutation.error}
      />

      {/* Delete Confirmation */}
      <ConfirmModal
        isOpen={!!deletingUser}
        onClose={() => setDeletingUser(null)}
        onConfirm={handleConfirmDelete}
        title={t("users.deleteUser")}
        message={t("users.deleteConfirmation")}
        detail={deletingUser?.email}
        variant="danger"
        confirmLabel={t("common.delete")}
        cancelLabel={t("common.cancel")}
        isLoading={deleteMutation.isPending}
      />
    </motion.div>
  );
}
