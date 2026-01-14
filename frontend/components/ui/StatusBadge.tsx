"use client";

/**
 * All known status values. The component gracefully handles unknown statuses
 * with fallback styling, but known statuses get specific styling.
 * Note: Image statuses are UPPERCASE (READY, ERROR, etc.) while experiment
 * statuses are lowercase (active, completed, etc.)
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
  | "extracting_features"
  // Uppercase variants for Image status
  | "UPLOADING"
  | "UPLOADED"
  | "PROCESSING"
  | "READY"
  | "ERROR"
  | "DETECTING"
  | "EXTRACTING_FEATURES";

interface StatusBadgeProps {
  status: Status;
  className?: string;
}

const statusStyles: Record<string, string> = {
  // Experiment statuses (lowercase)
  active: "bg-primary-500/20 text-primary-400",
  completed: "bg-accent-cyan/20 text-accent-cyan",
  ready: "bg-primary-500/20 text-primary-400",
  processing: "bg-accent-amber/20 text-accent-amber",
  detecting: "bg-accent-amber/20 text-accent-amber",
  extracting_features: "bg-accent-amber/20 text-accent-amber",
  error: "bg-accent-red/20 text-accent-red",
  draft: "bg-text-muted/20 text-text-muted",
  archived: "bg-text-muted/20 text-text-muted",
  // Image statuses (uppercase)
  UPLOADING: "bg-accent-amber/20 text-accent-amber",
  UPLOADED: "bg-accent-blue/20 text-accent-blue",
  PROCESSING: "bg-accent-amber/20 text-accent-amber",
  READY: "bg-primary-500/20 text-primary-400",
  ERROR: "bg-accent-red/20 text-accent-red",
  DETECTING: "bg-accent-amber/20 text-accent-amber",
  EXTRACTING_FEATURES: "bg-accent-amber/20 text-accent-amber",
};

export function getStatusStyles(status: Status): string {
  return statusStyles[status] ?? "bg-text-muted/20 text-text-muted";
}

export function StatusBadge({ status, className = "" }: StatusBadgeProps): JSX.Element {
  const displayStatus = status.replace(/_/g, " ").toLowerCase();
  return (
    <span
      className={`px-2 py-1 rounded-full text-xs font-medium capitalize ${getStatusStyles(status)} ${className}`}
    >
      {displayStatus}
    </span>
  );
}
