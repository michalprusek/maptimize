"use client";

import React, { Component, ReactNode } from "react";
import { AlertTriangle } from "lucide-react";

interface ErrorBoundaryProps {
  children: ReactNode;
  fallback?: ReactNode;
  onError?: (error: Error, errorInfo: React.ErrorInfo) => void;
}

interface ErrorBoundaryState {
  hasError: boolean;
  error?: Error;
}

/**
 * Error Boundary component to catch React rendering errors.
 *
 * Usage:
 * <ErrorBoundary fallback={<div>Something went wrong</div>}>
 *   <ComponentThatMightFail />
 * </ErrorBoundary>
 */
export class ErrorBoundary extends Component<ErrorBoundaryProps, ErrorBoundaryState> {
  constructor(props: ErrorBoundaryProps) {
    super(props);
    this.state = { hasError: false };
  }

  static getDerivedStateFromError(error: Error): ErrorBoundaryState {
    return { hasError: true, error };
  }

  componentDidCatch(error: Error, errorInfo: React.ErrorInfo) {
    // Log to console in development
    console.error("ErrorBoundary caught an error:", error, errorInfo);
    // Call optional error handler
    this.props.onError?.(error, errorInfo);
  }

  render() {
    if (this.state.hasError) {
      // Return custom fallback or default error UI
      return (
        this.props.fallback || (
          <div className="flex items-center gap-2 p-2 text-sm text-amber-500 bg-amber-500/10 rounded">
            <AlertTriangle className="h-4 w-4 flex-shrink-0" />
            <span>Failed to render content</span>
          </div>
        )
      );
    }

    return this.props.children;
  }
}

/**
 * Specialized Error Boundary for Markdown content.
 * Shows a friendly message when markdown rendering fails.
 */
export function MarkdownErrorBoundary({ children }: { children: ReactNode }) {
  return (
    <ErrorBoundary
      fallback={
        <div className="flex items-center gap-2 p-3 text-sm text-amber-500 bg-amber-500/10 rounded border border-amber-500/20">
          <AlertTriangle className="h-4 w-4 flex-shrink-0" />
          <span>Could not display message content. The message format may be invalid.</span>
        </div>
      }
      onError={(error) => {
        console.error("Markdown rendering failed:", error.message);
      }}
    >
      {children}
    </ErrorBoundary>
  );
}

export default ErrorBoundary;
