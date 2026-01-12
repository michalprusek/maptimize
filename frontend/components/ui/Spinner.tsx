"use client";

interface SpinnerProps {
  size?: "sm" | "md" | "lg";
  className?: string;
}

const sizeClasses = {
  sm: "w-6 h-6 border-2",
  md: "w-10 h-10 border-2",
  lg: "w-12 h-12 border-4",
};

export function Spinner({ size = "md", className = "" }: SpinnerProps): JSX.Element {
  return (
    <div
      className={`${sizeClasses[size]} border-primary-500 border-t-transparent rounded-full animate-spin ${className}`}
    />
  );
}

interface LoadingContainerProps {
  isLoading: boolean;
  children: React.ReactNode;
  spinnerSize?: "sm" | "md" | "lg";
  className?: string;
}

export function LoadingContainer({
  isLoading,
  children,
  spinnerSize = "md",
  className = "",
}: LoadingContainerProps): JSX.Element {
  if (isLoading) {
    return (
      <div className={`flex justify-center py-12 ${className}`}>
        <Spinner size={spinnerSize} />
      </div>
    );
  }
  return <>{children}</>;
}
