"use client";

type StatusType = "completed" | "active" | "pending" | "draft" | string;

interface AdminStatusBadgeProps {
  status: StatusType;
}

const statusStyles: Record<string, string> = {
  completed: "bg-green-500/20 text-green-400",
  active: "bg-blue-500/20 text-blue-400",
  pending: "bg-amber-500/20 text-amber-400",
  draft: "bg-gray-500/20 text-gray-400",
};

function getStatusStyle(status: StatusType): string {
  return statusStyles[status] || "bg-gray-500/20 text-gray-400";
}

export function AdminStatusBadge({ status }: AdminStatusBadgeProps): JSX.Element {
  return (
    <span className={`inline-flex px-2 py-0.5 rounded text-xs font-medium ${getStatusStyle(status)}`}>
      {status}
    </span>
  );
}
