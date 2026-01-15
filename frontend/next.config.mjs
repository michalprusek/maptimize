/**
 * Next.js configuration with internationalization support.
 */

import createNextIntlPlugin from "next-intl/plugin";

const withNextIntl = createNextIntlPlugin("./i18n/request.ts");

/** @type {import('next').NextConfig} */
const nextConfig = {
  output: "standalone",
  images: {
    remotePatterns: [
      {
        protocol: "http",
        hostname: "localhost",
        port: "8000",
      },
    ],
  },
  async rewrites() {
    // In production, nginx handles /uploads/ directly - no rewrite needed
    // The rewrite is only needed in development without nginx
    if (process.env.NODE_ENV === "production") {
      return [];
    }

    // Development: proxy /uploads to backend
    const backendUrl = process.env.INTERNAL_API_URL || "http://localhost:8000";
    return [
      {
        source: "/uploads/:path*",
        destination: `${backendUrl}/uploads/:path*`,
      },
    ];
  },
};

export default withNextIntl(nextConfig);
