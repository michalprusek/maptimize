"use client";

import { useEffect } from "react";
import { useRouter } from "next/navigation";
import { useQueryClient } from "@tanstack/react-query";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { motion } from "framer-motion";
import { useTranslations } from "next-intl";
import { useAuthStore } from "@/stores/authStore";
import { api } from "@/lib/api";
import {
  LayoutDashboard,
  FolderOpen,
  Scale,
  LogOut,
  User,
  ChevronRight,
  Settings,
} from "lucide-react";
import { Logo } from "@/components/ui";
import { clsx } from "clsx";

export default function DashboardLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  const router = useRouter();
  const pathname = usePathname();
  const queryClient = useQueryClient();
  const t = useTranslations("navigation");
  const { user, isAuthenticated, isLoading, checkAuth, logout } = useAuthStore();

  const navigation = [
    { name: t("dashboard"), href: "/dashboard", icon: LayoutDashboard },
    { name: t("experiments"), href: "/dashboard/experiments", icon: FolderOpen },
    { name: t("metrics"), href: "/dashboard/ranking", icon: Scale },
  ];

  useEffect(() => {
    checkAuth();
  }, [checkAuth]);

  useEffect(() => {
    if (!isLoading && !isAuthenticated) {
      router.push("/auth");
    }
  }, [isLoading, isAuthenticated, router]);

  if (isLoading) {
    return (
      <div className="min-h-screen flex items-center justify-center bg-bg-primary">
        <div className="w-12 h-12 border-4 border-primary-500 border-t-transparent rounded-full animate-spin" />
      </div>
    );
  }

  if (!isAuthenticated) {
    return null;
  }

  return (
    <div className="min-h-screen bg-bg-primary flex">
      {/* Sidebar */}
      <motion.aside
        initial={{ x: -100, opacity: 0 }}
        animate={{ x: 0, opacity: 1 }}
        className="fixed inset-y-0 left-0 w-64 bg-bg-secondary border-r border-white/5 flex flex-col"
      >
        {/* Logo */}
        <div className="p-6 border-b border-white/5">
          <Link href="/dashboard" className="flex items-center gap-3 group">
            <div className="p-2 bg-primary-500/10 rounded-xl transition-all duration-300 group-hover:bg-primary-500/20">
              <Logo size="md" className="text-primary-400" />
            </div>
            <span className="text-xl font-display font-bold text-gradient">
              MAPtimize
            </span>
          </Link>
        </div>

        {/* Navigation */}
        <nav className="flex-1 p-4 space-y-1">
          {navigation.map((item) => {
            const isActive = pathname === item.href;
            return (
              <Link
                key={item.name}
                href={item.href}
                className={clsx(
                  "flex items-center gap-3 px-4 py-3 rounded-xl transition-all duration-200",
                  isActive
                    ? "bg-primary-500/10 text-primary-400"
                    : "text-text-secondary hover:bg-white/5 hover:text-text-primary"
                )}
              >
                <item.icon className="w-5 h-5" />
                <span className="font-medium">{item.name}</span>
                {isActive && (
                  <ChevronRight className="w-4 h-4 ml-auto" />
                )}
              </Link>
            );
          })}
        </nav>

        {/* User section */}
        <div className="p-4 border-t border-white/5">
          <div className="flex items-center gap-3 px-4 py-3 mb-2">
            <div className="w-10 h-10 rounded-full bg-primary-500/20 flex items-center justify-center overflow-hidden">
              {user?.avatar_url ? (
                <img
                  src={api.getAvatarUrl(user.avatar_url)}
                  alt="Avatar"
                  className="w-full h-full object-cover"
                />
              ) : (
                <User className="w-5 h-5 text-primary-400" />
              )}
            </div>
            <div className="flex-1 min-w-0">
              <p className="text-sm font-medium text-text-primary truncate">
                {user?.name}
              </p>
              <p className="text-xs text-text-muted truncate">{user?.email}</p>
            </div>
            <div className="flex items-center gap-1">
              <Link
                href="/dashboard/settings"
                className="p-2 text-text-secondary hover:text-primary-400 hover:bg-white/5 rounded-lg transition-all duration-200"
                title={t("settings")}
              >
                <Settings className="w-5 h-5" />
              </Link>
              <button
                onClick={() => {
                  queryClient.clear();
                  logout();
                  router.push("/auth");
                }}
                className="p-2 text-text-secondary hover:text-accent-red hover:bg-accent-red/5 rounded-lg transition-all duration-200"
                title={t("signOut")}
              >
                <LogOut className="w-5 h-5" />
              </button>
            </div>
          </div>
        </div>
      </motion.aside>

      {/* Main content */}
      <main className="flex-1 ml-64">
        <div className="min-h-screen p-8">{children}</div>
      </main>
    </div>
  );
}
