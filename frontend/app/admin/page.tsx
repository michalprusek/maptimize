"use client";

import { useQuery } from "@tanstack/react-query";
import { useTranslations } from "next-intl";
import { useRouter } from "next/navigation";
import { motion } from "framer-motion";
import {
  Users,
  Microscope,
  Image as ImageIcon,
  HardDrive,
  Shield,
  Activity,
  ArrowRight,
} from "lucide-react";
import { api } from "@/lib/api";
import { formatBytes, formatDate } from "@/lib/utils";
import {
  AdminStatsCard,
  AdminTimelineChart,
  AdminStorageChart,
  AdminLoadingState,
  AdminErrorState,
  roleColors,
} from "@/components/admin";

const containerVariants = {
  hidden: { opacity: 0 },
  visible: {
    opacity: 1,
    transition: { staggerChildren: 0.1 },
  },
};

const itemVariants = {
  hidden: { opacity: 0, y: 20 },
  visible: { opacity: 1, y: 0 },
};

export default function AdminDashboardPage() {
  const t = useTranslations("admin");
  const router = useRouter();

  const { data: stats, isLoading: statsLoading, isError: statsError, refetch: refetchStats } = useQuery({
    queryKey: ["admin", "stats"],
    queryFn: () => api.getAdminStats(),
  });

  const { data: timeline, isLoading: timelineLoading, isError: timelineError, refetch: refetchTimeline } = useQuery({
    queryKey: ["admin", "timeline"],
    queryFn: () => api.getAdminTimelineStats(30),
  });

  const { data: users, isLoading: usersLoading, isError: usersError, refetch: refetchUsers } = useQuery({
    queryKey: ["admin", "users", "recent"],
    queryFn: () => api.getAdminUsers({ page: 1, page_size: 5, sort_by: "created_at", sort_order: "desc" }),
  });

  if (statsLoading) {
    return <AdminLoadingState />;
  }

  if (statsError) {
    return <AdminErrorState onRetry={() => refetchStats()} />;
  }

  return (
    <motion.div
      initial="hidden"
      animate="visible"
      variants={containerVariants}
      className="space-y-8"
    >
      {/* Header */}
      <motion.div variants={itemVariants}>
        <h1 className="text-2xl font-bold text-text-primary flex items-center gap-3">
          <Shield className="w-7 h-7 text-purple-400" />
          {t("title")}
        </h1>
        <p className="text-text-secondary mt-1">{t("subtitle")}</p>
      </motion.div>

      {/* Stats Grid */}
      <motion.div
        variants={itemVariants}
        className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4"
      >
        <AdminStatsCard
          title={t("stats.totalUsers")}
          value={stats?.total_users || 0}
          icon={Users}
          color="blue"
          description={`${stats?.admin_count || 0} admins, ${stats?.researcher_count || 0} researchers, ${stats?.viewer_count || 0} viewers`}
        />
        <AdminStatsCard
          title={t("stats.totalExperiments")}
          value={stats?.total_experiments || 0}
          icon={Microscope}
          color="green"
        />
        <AdminStatsCard
          title={t("stats.totalImages")}
          value={stats?.total_images || 0}
          icon={ImageIcon}
          color="purple"
        />
        <AdminStatsCard
          title={t("stats.totalStorage")}
          value={formatBytes(stats?.total_storage_bytes || 0)}
          icon={HardDrive}
          color="amber"
        />
      </motion.div>

      {/* Charts Row */}
      <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
        {/* Timeline Chart */}
        <motion.div variants={itemVariants} className="lg:col-span-2">
          <div className="glass-card p-6">
            <div className="flex items-center justify-between mb-6">
              <div>
                <h3 className="text-lg font-semibold text-text-primary flex items-center gap-2">
                  <Activity className="w-5 h-5 text-primary-400" />
                  {t("charts.activityTimeline")}
                </h3>
                <p className="text-sm text-text-muted">{t("charts.last30Days")}</p>
              </div>
            </div>
            {timelineLoading ? (
              <AdminLoadingState height="h-[300px]" />
            ) : timelineError ? (
              <AdminErrorState height="h-[300px]" iconSize="sm" onRetry={() => refetchTimeline()} />
            ) : timeline?.data ? (
              <AdminTimelineChart data={timeline.data} height={300} />
            ) : null}
          </div>
        </motion.div>

        {/* Storage Chart */}
        <motion.div variants={itemVariants}>
          <div className="glass-card p-6 h-full">
            <div className="mb-4">
              <h3 className="text-lg font-semibold text-text-primary flex items-center gap-2">
                <HardDrive className="w-5 h-5 text-amber-400" />
                {t("charts.storageBreakdown")}
              </h3>
            </div>
            {stats ? (
              <AdminStorageChart
                data={{
                  images_storage_bytes: stats.images_storage_bytes,
                  documents_storage_bytes: stats.documents_storage_bytes,
                }}
                height={200}
              />
            ) : null}
          </div>
        </motion.div>
      </div>

      {/* Recent Users */}
      <motion.div variants={itemVariants}>
        <div className="glass-card p-6">
          <div className="flex items-center justify-between mb-6">
            <div>
              <h3 className="text-lg font-semibold text-text-primary flex items-center gap-2">
                <Users className="w-5 h-5 text-blue-400" />
                {t("recentUsers.title")}
              </h3>
              <p className="text-sm text-text-muted">{t("recentUsers.subtitle")}</p>
            </div>
            <button
              onClick={() => router.push("/admin/users")}
              className="flex items-center gap-2 text-sm text-primary-400 hover:text-primary-300 transition-colors"
            >
              {t("recentUsers.viewAll")}
              <ArrowRight className="w-4 h-4" />
            </button>
          </div>

          {usersLoading ? (
            <AdminLoadingState height="py-8" />
          ) : usersError ? (
            <AdminErrorState height="py-8" iconSize="sm" onRetry={() => refetchUsers()} />
          ) : (
            <div className="overflow-x-auto">
              <table className="w-full">
                <thead>
                  <tr className="border-b border-white/10">
                    <th className="px-4 py-2 text-left text-xs font-medium text-text-secondary uppercase">
                      {t("users.table.user")}
                    </th>
                    <th className="px-4 py-2 text-left text-xs font-medium text-text-secondary uppercase">
                      {t("users.table.role")}
                    </th>
                    <th className="px-4 py-2 text-left text-xs font-medium text-text-secondary uppercase">
                      {t("users.table.registered")}
                    </th>
                    <th className="px-4 py-2 text-right text-xs font-medium text-text-secondary uppercase">
                      {t("users.table.experiments")}
                    </th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-white/5">
                  {users?.users.map((user) => (
                    <tr
                      key={user.id}
                      className="hover:bg-white/5 cursor-pointer transition-colors"
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
                        <span className={`inline-flex items-center px-2 py-0.5 rounded text-xs font-medium ${roleColors[user.role]}`}>
                          {user.role}
                        </span>
                      </td>
                      <td className="px-4 py-3 text-sm text-text-secondary">
                        {formatDate(user.created_at)}
                      </td>
                      <td className="px-4 py-3 text-sm text-text-secondary text-right">
                        {user.experiment_count}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>
      </motion.div>
    </motion.div>
  );
}
