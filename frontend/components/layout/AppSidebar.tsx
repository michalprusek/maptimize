"use client";

/**
 * AppSidebar Component
 *
 * Shared sidebar for the application. Can be used as:
 * - Fixed sidebar (default, used in dashboard layout)
 * - Overlay sidebar (for editor and other full-screen views)
 */

import { useState } from "react";
import { useRouter, usePathname } from "next/navigation";
import { useQueryClient } from "@tanstack/react-query";
import Link from "next/link";
import { motion } from "framer-motion";
import { useTranslations } from "next-intl";
import {
  navStaggerContainerVariants,
  navItemVariants,
  layoutTransition,
} from "@/lib/animations";
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
  Bug,
  Dna,
} from "lucide-react";
import { Logo, BugReportModal } from "@/components/ui";
import { clsx } from "clsx";

interface AppSidebarProps {
  /**
   * Display variant:
   * - "fixed": Fixed sidebar (default, used in dashboard)
   * - "overlay": Floating overlay sidebar (used in editor)
   */
  variant?: "fixed" | "overlay";
  /**
   * Callback when user closes the sidebar (only for overlay variant)
   */
  onClose?: () => void;
  /**
   * Custom active path for navigation highlighting
   * If not provided, uses current pathname
   */
  activePath?: string;
}

export function AppSidebar({
  variant = "fixed",
  onClose,
  activePath,
}: AppSidebarProps) {
  const router = useRouter();
  const pathname = usePathname();
  const queryClient = useQueryClient();
  const t = useTranslations("navigation");
  const tBug = useTranslations("bugReport");
  const { user, logout } = useAuthStore();
  const [showBugReport, setShowBugReport] = useState(false);

  const currentPath = activePath ?? pathname;

  const navigation = [
    { name: t("dashboard"), href: "/dashboard", icon: LayoutDashboard },
    { name: t("experiments"), href: "/dashboard/experiments", icon: FolderOpen },
    { name: t("proteins"), href: "/dashboard/proteins", icon: Dna },
    { name: t("metrics"), href: "/dashboard/ranking", icon: Scale },
  ];

  const handleLogout = () => {
    queryClient.clear();
    logout();
    router.push("/auth");
  };

  const handleNavClick = (href: string) => {
    if (variant === "overlay" && onClose) {
      onClose();
    }
    router.push(href);
  };

  const isFixed = variant === "fixed";

  const sidebarContent = (
    <>
      {/* Logo */}
      <div className="p-6 border-b border-white/5">
        <Link
          href="/dashboard"
          className="flex items-center gap-3 group"
          onClick={variant === "overlay" ? onClose : undefined}
        >
          <div className="p-2 bg-primary-500/10 rounded-xl transition-all duration-300 group-hover:bg-primary-500/20">
            <Logo size="md" className="text-primary-400" />
          </div>
          <span className="text-xl font-display font-bold text-gradient">
            MAPtimize
          </span>
        </Link>
      </div>

      {/* Navigation */}
      <motion.nav
        className="flex-1 p-4 space-y-1"
        variants={navStaggerContainerVariants}
        initial="hidden"
        animate="visible"
      >
        {navigation.map((item) => {
          const isActive = currentPath === item.href ||
            (item.href !== "/dashboard" && currentPath.startsWith(item.href));

          if (isFixed) {
            return (
              <motion.div key={item.name} variants={navItemVariants} className="relative">
                {/* Sliding active background */}
                {isActive && (
                  <motion.div
                    layoutId="nav-active-bg"
                    className="absolute inset-0 bg-primary-500/10 rounded-xl"
                    transition={layoutTransition}
                  />
                )}
                <Link
                  href={item.href}
                  className={clsx(
                    "relative flex items-center gap-3 px-4 py-3 rounded-xl transition-colors duration-200",
                    isActive
                      ? "text-primary-400"
                      : "text-text-secondary hover:bg-white/5 hover:text-text-primary"
                  )}
                >
                  <item.icon className="w-5 h-5" />
                  <span className="font-medium">{item.name}</span>
                  {isActive && <ChevronRight className="w-4 h-4 ml-auto" />}
                </Link>
              </motion.div>
            );
          }

          return (
            <motion.div key={item.name} variants={navItemVariants} className="relative">
              {isActive && (
                <motion.div
                  layoutId="nav-active-bg-overlay"
                  className="absolute inset-0 bg-primary-500/10 rounded-xl"
                  transition={layoutTransition}
                />
              )}
              <button
                onClick={() => handleNavClick(item.href)}
                className={clsx(
                  "relative w-full flex items-center gap-3 px-4 py-3 rounded-xl transition-colors duration-200",
                  isActive
                    ? "text-primary-400"
                    : "text-text-secondary hover:bg-white/5 hover:text-text-primary"
                )}
              >
                <item.icon className="w-5 h-5" />
                <span className="font-medium">{item.name}</span>
                {isActive && <ChevronRight className="w-4 h-4 ml-auto" />}
              </button>
            </motion.div>
          );
        })}
      </motion.nav>

      {/* User section */}
      <div className="p-4 border-t border-white/5">
        <div className="flex items-center gap-3 px-4 py-3 mb-2">
          <div className="w-10 h-10 rounded-full bg-primary-500/20 flex items-center justify-center overflow-hidden">
            {user?.avatar_url && api.getAvatarUrl(user.avatar_url) ? (
              <img
                src={api.getAvatarUrl(user.avatar_url)}
                alt="Avatar"
                className="w-full h-full object-cover"
                onError={(e) => {
                  (e.target as HTMLImageElement).style.display = "none";
                }}
              />
            ) : (
              <User className="w-5 h-5 text-primary-400" />
            )}
          </div>
          <div className="flex items-center gap-1">
            <button
              onClick={() => setShowBugReport(true)}
              className="p-2 text-text-secondary hover:text-accent-amber hover:bg-accent-amber/5 rounded-lg transition-all duration-200"
              title={tBug("reportBug")}
            >
              <Bug className="w-5 h-5" />
            </button>
            {isFixed ? (
              <Link
                href="/dashboard/settings"
                className="p-2 text-text-secondary hover:text-primary-400 hover:bg-white/5 rounded-lg transition-all duration-200"
                title={t("settings")}
              >
                <Settings className="w-5 h-5" />
              </Link>
            ) : (
              <button
                onClick={() => handleNavClick("/dashboard/settings")}
                className="p-2 text-text-secondary hover:text-primary-400 hover:bg-white/5 rounded-lg transition-all duration-200"
                title={t("settings")}
              >
                <Settings className="w-5 h-5" />
              </button>
            )}
            <button
              onClick={handleLogout}
              className="p-2 text-text-secondary hover:text-accent-red hover:bg-accent-red/5 rounded-lg transition-all duration-200"
              title={t("signOut")}
            >
              <LogOut className="w-5 h-5" />
            </button>
          </div>
        </div>
      </div>

      {/* Bug Report Modal */}
      <BugReportModal
        isOpen={showBugReport}
        onClose={() => setShowBugReport(false)}
      />
    </>
  );

  if (isFixed) {
    return (
      <motion.aside
        initial={{ x: -100, opacity: 0 }}
        animate={{ x: 0, opacity: 1 }}
        className="fixed inset-y-0 left-0 w-64 bg-bg-secondary border-r border-white/5 flex flex-col"
      >
        {sidebarContent}
      </motion.aside>
    );
  }

  // Overlay variant
  return (
    <motion.aside
      initial={{ x: -264, opacity: 0 }}
      animate={{ x: 0, opacity: 1 }}
      exit={{ x: -264, opacity: 0 }}
      transition={{ type: "spring", damping: 25, stiffness: 300 }}
      className="absolute left-0 top-0 bottom-0 w-64 bg-bg-secondary z-40 border-r border-white/5 flex flex-col"
    >
      {sidebarContent}
    </motion.aside>
  );
}
