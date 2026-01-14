/**
 * Next.js middleware for internationalization.
 *
 * NOTE: Locale-based routing is disabled until app is restructured
 * to use [locale] folder structure. Language switching currently
 * works via localStorage/settings store without URL prefixes.
 */

import { NextResponse } from "next/server";
import type { NextRequest } from "next/server";

export function middleware(request: NextRequest) {
  // Pass through all requests without modification
  return NextResponse.next();
}

export const config = {
  // Match nothing - middleware is effectively disabled
  matcher: [],
};
