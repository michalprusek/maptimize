"use client";

import { useEffect } from "react";
import { useTranslations } from "next-intl";

export default function Error({
  error,
  reset,
}: {
  error: Error & { digest?: string };
  reset: () => void;
}) {
  const t = useTranslations("common");

  useEffect(() => {
    console.error("Application error:", error);
  }, [error]);

  return (
    <div style={{ padding: "2rem", textAlign: "center" }}>
      <h2>{t("somethingWentWrong")}</h2>
      <p style={{ color: "#666", marginTop: "0.5rem" }}>
        {t("errorDescription")}
      </p>
      {error.digest && (
        <p style={{ color: "#999", fontSize: "0.8rem", marginTop: "0.5rem" }}>
          {t("errorReference", { digest: error.digest })}
        </p>
      )}
      <button onClick={() => reset()} style={{ marginTop: "1rem" }}>
        {t("tryAgain")}
      </button>
    </div>
  );
}
