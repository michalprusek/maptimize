"use client";

type Status = "active" | "completed" | "draft" | "archived" | "processing" | "ready" | "error";

interface StatusBadgeProps {
  status: Status | string;
  className?: string;
}

const statusStyles: Record<string, string> = {
  active: "bg-primary-500/20 text-primary-400",
  completed: "bg-accent-cyan/20 text-accent-cyan",
  ready: "bg-primary-500/20 text-primary-400",
  processing: "bg-accent-amber/20 text-accent-amber",
  detecting: "bg-accent-amber/20 text-accent-amber",
  extracting_features: "bg-accent-amber/20 text-accent-amber",
  error: "bg-accent-red/20 text-accent-red",
  draft: "bg-text-muted/20 text-text-muted",
  archived: "bg-text-muted/20 text-text-muted",
};

export function getStatusStyles(status: string): string {
  return statusStyles[status] || "bg-text-muted/20 text-text-muted";
}

export function StatusBadge({ status, className = "" }: StatusBadgeProps): JSX.Element {
  return (
    <span
      className={`px-2 py-1 rounded-full text-xs font-medium ${getStatusStyles(status)} ${className}`}
    >
      {status}
    </span>
  );
}
