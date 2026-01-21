"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import {
  Search,
  ChevronUp,
  ChevronDown,
  Edit,
  Trash2,
  Eye,
} from "lucide-react";
import type { AdminUserListItem, AdminUserListResponse, UserRole } from "@/lib/api";
import { formatBytes, formatDate } from "@/lib/utils";
import { roleColors, roleIcons } from "./constants";

interface AdminUserTableProps {
  data: AdminUserListResponse;
  onPageChange: (page: number) => void;
  onSearch: (search: string) => void;
  onSort: (sortBy: string, sortOrder: "asc" | "desc") => void;
  onRoleFilter: (role: UserRole | undefined) => void;
  onEditUser: (user: AdminUserListItem) => void;
  onDeleteUser: (user: AdminUserListItem) => void;
  sortBy: string;
  sortOrder: "asc" | "desc";
  searchQuery: string;
  roleFilter?: UserRole;
  isDeleting?: boolean;
}

export function AdminUserTable({
  data,
  onPageChange,
  onSearch,
  onSort,
  onRoleFilter,
  onEditUser,
  onDeleteUser,
  sortBy,
  sortOrder,
  searchQuery,
  roleFilter,
  isDeleting,
}: AdminUserTableProps) {
  const router = useRouter();
  const [localSearch, setLocalSearch] = useState(searchQuery);

  const handleSearchSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    onSearch(localSearch);
  };

  const handleSort = (column: string) => {
    if (sortBy === column) {
      onSort(column, sortOrder === "asc" ? "desc" : "asc");
    } else {
      onSort(column, "desc");
    }
  };

  const SortIcon = ({ column }: { column: string }) => {
    if (sortBy !== column) return null;
    return sortOrder === "asc" ? (
      <ChevronUp className="w-4 h-4" />
    ) : (
      <ChevronDown className="w-4 h-4" />
    );
  };

  return (
    <div className="space-y-4">
      {/* Search and filters */}
      <div className="flex flex-col sm:flex-row gap-4">
        <form onSubmit={handleSearchSubmit} className="flex-1">
          <div className="relative">
            <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-text-muted" />
            <input
              type="text"
              placeholder="Search by name or email..."
              value={localSearch}
              onChange={(e) => setLocalSearch(e.target.value)}
              className="input-field pl-10 w-full"
            />
          </div>
        </form>
        <select
          value={roleFilter || ""}
          onChange={(e) => onRoleFilter(e.target.value as UserRole || undefined)}
          className="input-field w-40"
        >
          <option value="">All roles</option>
          <option value="admin">Admin</option>
          <option value="researcher">Researcher</option>
          <option value="viewer">Viewer</option>
        </select>
      </div>

      {/* Table */}
      <div className="glass-card overflow-hidden">
        <div className="overflow-x-auto">
          <table className="w-full">
            <thead>
              <tr className="border-b border-white/10">
                <th
                  className="px-4 py-3 text-left text-xs font-medium text-text-secondary uppercase tracking-wider cursor-pointer hover:text-text-primary"
                  onClick={() => handleSort("name")}
                >
                  <div className="flex items-center gap-1">
                    User
                    <SortIcon column="name" />
                  </div>
                </th>
                <th className="px-4 py-3 text-left text-xs font-medium text-text-secondary uppercase tracking-wider">
                  Role
                </th>
                <th
                  className="px-4 py-3 text-left text-xs font-medium text-text-secondary uppercase tracking-wider cursor-pointer hover:text-text-primary"
                  onClick={() => handleSort("created_at")}
                >
                  <div className="flex items-center gap-1">
                    Registered
                    <SortIcon column="created_at" />
                  </div>
                </th>
                <th
                  className="px-4 py-3 text-left text-xs font-medium text-text-secondary uppercase tracking-wider cursor-pointer hover:text-text-primary"
                  onClick={() => handleSort("last_login")}
                >
                  <div className="flex items-center gap-1">
                    Last Login
                    <SortIcon column="last_login" />
                  </div>
                </th>
                <th className="px-4 py-3 text-right text-xs font-medium text-text-secondary uppercase tracking-wider">
                  Experiments
                </th>
                <th className="px-4 py-3 text-right text-xs font-medium text-text-secondary uppercase tracking-wider">
                  Images
                </th>
                <th className="px-4 py-3 text-right text-xs font-medium text-text-secondary uppercase tracking-wider">
                  Storage
                </th>
                <th className="px-4 py-3 text-right text-xs font-medium text-text-secondary uppercase tracking-wider">
                  Actions
                </th>
              </tr>
            </thead>
            <tbody className="divide-y divide-white/5">
              {data.users.map((user) => {
                const RoleIcon = roleIcons[user.role];
                return (
                  <tr
                    key={user.id}
                    className="hover:bg-white/5 transition-colors cursor-pointer"
                    onClick={() => router.push(`/admin/users/${user.id}`)}
                  >
                    <td className="px-4 py-3">
                      <div className="flex items-center gap-3">
                        <div className="w-8 h-8 rounded-full bg-primary-500/20 flex items-center justify-center text-primary-400 text-sm font-medium">
                          {user.name.charAt(0).toUpperCase()}
                        </div>
                        <div>
                          <p className="text-sm font-medium text-text-primary">{user.name}</p>
                          <p className="text-xs text-text-muted">{user.email}</p>
                        </div>
                      </div>
                    </td>
                    <td className="px-4 py-3">
                      <span className={`inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full text-xs font-medium border ${roleColors[user.role]}`}>
                        <RoleIcon className="w-3 h-3" />
                        {user.role}
                      </span>
                    </td>
                    <td className="px-4 py-3 text-sm text-text-secondary">
                      {formatDate(user.created_at)}
                    </td>
                    <td className="px-4 py-3 text-sm text-text-secondary">
                      {formatDate(user.last_login)}
                    </td>
                    <td className="px-4 py-3 text-sm text-text-secondary text-right">
                      {user.experiment_count}
                    </td>
                    <td className="px-4 py-3 text-sm text-text-secondary text-right">
                      {user.image_count}
                    </td>
                    <td className="px-4 py-3 text-sm text-text-secondary text-right">
                      {formatBytes(user.storage_bytes)}
                    </td>
                    <td className="px-4 py-3 text-right">
                      <div className="flex items-center justify-end gap-1" onClick={(e) => e.stopPropagation()}>
                        <button
                          onClick={() => router.push(`/admin/users/${user.id}`)}
                          className="p-1.5 rounded-lg hover:bg-white/10 text-text-secondary hover:text-text-primary transition-colors"
                          title="View details"
                        >
                          <Eye className="w-4 h-4" />
                        </button>
                        <button
                          onClick={() => onEditUser(user)}
                          className="p-1.5 rounded-lg hover:bg-white/10 text-text-secondary hover:text-primary-400 transition-colors"
                          title="Edit user"
                        >
                          <Edit className="w-4 h-4" />
                        </button>
                        <button
                          onClick={() => onDeleteUser(user)}
                          className="p-1.5 rounded-lg hover:bg-white/10 text-text-secondary hover:text-accent-red transition-colors"
                          title="Delete user"
                          disabled={isDeleting}
                        >
                          <Trash2 className="w-4 h-4" />
                        </button>
                      </div>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>

        {/* Pagination */}
        {data.total_pages > 1 && (
          <div className="px-4 py-3 border-t border-white/10 flex items-center justify-between">
            <p className="text-sm text-text-muted">
              Showing {(data.page - 1) * data.page_size + 1} to{" "}
              {Math.min(data.page * data.page_size, data.total)} of {data.total} users
            </p>
            <div className="flex items-center gap-2">
              <button
                onClick={() => onPageChange(data.page - 1)}
                disabled={data.page === 1}
                className="px-3 py-1.5 text-sm rounded-lg border border-white/10 hover:bg-white/5 disabled:opacity-50 disabled:cursor-not-allowed"
              >
                Previous
              </button>
              <span className="text-sm text-text-secondary">
                Page {data.page} of {data.total_pages}
              </span>
              <button
                onClick={() => onPageChange(data.page + 1)}
                disabled={data.page >= data.total_pages}
                className="px-3 py-1.5 text-sm rounded-lg border border-white/10 hover:bg-white/5 disabled:opacity-50 disabled:cursor-not-allowed"
              >
                Next
              </button>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
