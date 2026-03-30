"use client";

import { useEffect } from "react";

/**
 * Global error boundary — renders outside all providers (including i18n).
 * Hardcoded fallback text is acceptable here since useTranslations is unavailable.
 */
export default function GlobalError({
  error,
  reset,
}: {
  error: Error & { digest?: string };
  reset: () => void;
}) {
  useEffect(() => {
    console.error("Global application error:", error);
  }, [error]);

  return (
    <html>
      <body>
        <div style={{ padding: "2rem", textAlign: "center" }}>
          <h2>Something went wrong</h2>
          <p style={{ color: "#666", marginTop: "0.5rem" }}>
            An unexpected error occurred. Please try again or refresh the page.
          </p>
          {error.digest && (
            <p style={{ color: "#999", fontSize: "0.8rem", marginTop: "0.5rem" }}>
              Error reference: {error.digest}
            </p>
          )}
          <button onClick={() => reset()} style={{ marginTop: "1rem" }}>
            Try again
          </button>
        </div>
      </body>
    </html>
  );
}
