import type { Metadata } from "next";
import "./globals.css";
import { Providers } from "./providers";

export const metadata: Metadata = {
  title: "MAPtimize - Microtubule Analysis Platform",
  description: "Advanced analysis platform for microtubule microscopy images",
};

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html lang="en" className="dark">
      <body className="min-h-screen bg-bg-primary">
        <Providers>{children}</Providers>
      </body>
    </html>
  );
}
