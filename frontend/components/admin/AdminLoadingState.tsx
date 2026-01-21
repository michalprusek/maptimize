"use client";

import { Spinner } from "@/components/ui";

interface AdminLoadingStateProps {
  size?: "sm" | "md" | "lg";
  height?: string;
}

export function AdminLoadingState({ size = "lg", height = "h-64" }: AdminLoadingStateProps): JSX.Element {
  return (
    <div className={`flex items-center justify-center ${height}`}>
      <Spinner size={size} />
    </div>
  );
}
