"use client";

import { LucideIcon } from "lucide-react";

interface AdminStatsCardProps {
  title: string;
  value: string | number;
  icon: LucideIcon;
  description?: string;
  trend?: {
    value: number;
    label: string;
  };
  color?: "blue" | "green" | "purple" | "amber";
}

const colorStyles = {
  blue: {
    bg: "bg-primary-500/10",
    icon: "text-primary-400",
    border: "border-primary-500/20",
  },
  green: {
    bg: "bg-green-500/10",
    icon: "text-green-400",
    border: "border-green-500/20",
  },
  purple: {
    bg: "bg-purple-500/10",
    icon: "text-purple-400",
    border: "border-purple-500/20",
  },
  amber: {
    bg: "bg-amber-500/10",
    icon: "text-amber-400",
    border: "border-amber-500/20",
  },
};

export function AdminStatsCard({
  title,
  value,
  icon: Icon,
  description,
  trend,
  color = "blue",
}: AdminStatsCardProps) {
  const styles = colorStyles[color];

  return (
    <div className={`glass-card p-6 border ${styles.border}`}>
      <div className="flex items-start justify-between">
        <div className="flex-1">
          <p className="text-sm text-text-secondary mb-1">{title}</p>
          <p className="text-3xl font-bold text-text-primary">{value}</p>
          {description && (
            <p className="text-xs text-text-muted mt-1">{description}</p>
          )}
          {trend && (
            <p className={`text-xs mt-2 ${trend.value >= 0 ? "text-green-400" : "text-red-400"}`}>
              {trend.value >= 0 ? "+" : ""}{trend.value}% {trend.label}
            </p>
          )}
        </div>
        <div className={`${styles.bg} p-3 rounded-xl`}>
          <Icon className={`w-6 h-6 ${styles.icon}`} />
        </div>
      </div>
    </div>
  );
}
