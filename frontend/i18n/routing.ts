/**
 * Internationalization routing configuration.
 *
 * Supported locales: English (en), French (fr)
 * Default locale: English (en)
 *
 * Uses locale prefix strategy "as-needed" - English URLs don't have /en/ prefix,
 * but French URLs will have /fr/ prefix.
 */

import { defineRouting } from "next-intl/routing";

export const locales = ["en", "fr"] as const;
export type Locale = (typeof locales)[number];

export const routing = defineRouting({
  locales,
  defaultLocale: "en",
  localePrefix: "as-needed",
});
