"use client";

/**
 * All known status values. The component gracefully handles unknown statuses
 * with fallback styling, but known statuses get specific styling.
 */
type Status =
  | "active"
  | "completed"
  | "draft"
  | "archived"
  | "processing"
  | "ready"
  | "error"
  | "detecting"
  | "extracting_features";

interface StatusBadgeProps {
  status: Status;
  className?: string;
}

const statusStyles = {
  active: "bg-primary-500/20 text-primary-400",
  completed: "bg-accent-cyan/20 text-accent-cyan",
  ready: "bg-primary-500/20 text-primary-400",
  processing: "bg-accent-amber/20 text-accent-amber",
  detecting: "bg-accent-amber/20 text-accent-amber",
  extracting_features: "bg-accent-amber/20 text-accent-amber",
  error: "bg-accent-red/20 text-accent-red",
  draft: "bg-text-muted/20 text-text-muted",
  archived: "bg-text-muted/20 text-text-muted",
} satisfies Record<Status, string>;

export function getStatusStyles(status: Status): string {
  return statusStyles[status] ?? "bg-text-muted/20 text-text-muted";
}

export function StatusBadge({ status, className = "" }: StatusBadgeProps): JSX.Element {
  const displayStatus = status.replace(/_/g, " ");
  return (
    <span
      className={`px-2 py-1 rounded-full text-xs font-medium capitalize ${getStatusStyles(status)} ${className}`}
    >
      {displayStatus}
    </span>
  );
}
