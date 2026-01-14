/**
 * Per-request internationalization configuration.
 *
 * This file is used by next-intl to determine the locale and load
 * the appropriate messages for each request.
 */

import { getRequestConfig } from "next-intl/server";
import { routing, Locale, locales } from "./routing";

export default getRequestConfig(async ({ requestLocale }) => {
  // Validate the requested locale
  let locale = await requestLocale;

  // Ensure that a valid locale is used
  if (!locale || !locales.includes(locale as Locale)) {
    locale = routing.defaultLocale;
  }

  return {
    locale,
    messages: (await import(`../messages/${locale}.json`)).default,
  };
});
