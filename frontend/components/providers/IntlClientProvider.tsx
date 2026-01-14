"use client";

import { NextIntlClientProvider } from "next-intl";
import { useSettingsStore } from "@/stores/settingsStore";
import { useState, useEffect } from "react";

// Import messages statically to avoid async loading issues
import enMessages from "@/messages/en.json";
import frMessages from "@/messages/fr.json";

const messagesMap = {
  en: enMessages,
  fr: frMessages,
} as const;

// Helper to get/clear saved scroll position
function getSavedScrollY(): number | undefined {
  const win = window as unknown as { __savedScrollY?: number };
  const value = win.__savedScrollY;
  if (value !== undefined) {
    delete win.__savedScrollY;
  }
  return value;
}

// Component that restores scroll position after mount
// This runs INSIDE the provider, so it mounts after the key change
function ScrollRestorer() {
  useEffect(() => {
    const savedScrollY = getSavedScrollY();
    if (savedScrollY !== undefined && savedScrollY > 0) {
      // Timeout ensures browser has finished layout after React remount
      const timeoutId = setTimeout(() => {
        window.scrollTo({ top: savedScrollY, behavior: "instant" });
      }, 50);
      return () => clearTimeout(timeoutId);
    }
  }, []); // Empty deps - only runs on mount

  return null;
}

export function IntlClientProvider({
  children,
}: {
  children: React.ReactNode;
}) {
  const language = useSettingsStore((state) => state.language);
  const [mounted, setMounted] = useState(false);

  useEffect(() => {
    setMounted(true);
  }, []);

  // Avoid hydration mismatch - use default locale until mounted
  const locale = mounted ? language : "en";
  const messages = messagesMap[locale];

  return (
    <NextIntlClientProvider
      key={locale} // Forces re-render on language change
      locale={locale}
      messages={messages}
      timeZone="UTC"
    >
      <ScrollRestorer key={`scroll-${locale}`} />
      {children}
    </NextIntlClientProvider>
  );
}
