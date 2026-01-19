import { type ClassValue, clsx } from "clsx";
import { twMerge } from "tailwind-merge";

export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs));
}

export function formatBytes(bytes: number, decimals = 2) {
  if (bytes === 0) return "0 Bytes";

  const k = 1024;
  const dm = decimals < 0 ? 0 : decimals;
  const sizes = ["Bytes", "KB", "MB", "GB"];

  const i = Math.floor(Math.log(bytes) / Math.log(k));

  return parseFloat((bytes / Math.pow(k, i)).toFixed(dm)) + " " + sizes[i];
}

export function formatDate(date: string | Date) {
  return new Date(date).toLocaleDateString("en-US", {
    year: "numeric",
    month: "short",
    day: "numeric",
  });
}

export function formatDateTime(date: string | Date) {
  return new Date(date).toLocaleString("en-US", {
    year: "numeric",
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}

export interface ProcessedImageUrl {
  url: string;
  isMicroscopy: boolean;
  isBase64: boolean;
}

/**
 * Sanitize URL for logging - removes sensitive tokens from query parameters.
 * SECURITY: Never log full URLs that may contain authentication tokens.
 */
export function sanitizeUrlForLogging(url: string): string {
  try {
    const urlObj = new URL(url);
    // Remove sensitive query parameters
    urlObj.searchParams.delete("token");
    urlObj.searchParams.delete("access_token");
    urlObj.searchParams.delete("api_key");
    // Replace remaining sensitive-looking values
    urlObj.searchParams.forEach((value, key) => {
      if (key.toLowerCase().includes("token") || key.toLowerCase().includes("key")) {
        urlObj.searchParams.set(key, "[REDACTED]");
      }
    });
    return urlObj.toString();
  } catch {
    // If URL parsing fails, just remove obvious token patterns
    return url.replace(/[?&]token=[^&]+/gi, "[TOKEN_REDACTED]");
  }
}

/**
 * Process image URL to add backend prefix and auth token if needed.
 * Handles API paths, uploads paths, and base64 images.
 */
export function processImageUrl(src: string): ProcessedImageUrl {
  let imageSrc = src || "";

  // Normalize URL - ensure it starts with /
  if (imageSrc.startsWith("api/")) {
    imageSrc = "/" + imageSrc;
  }

  const isApiImage = imageSrc.startsWith("/api/");
  const isBase64Image = imageSrc.startsWith("data:image/");
  const isUploadsImage = imageSrc.startsWith("/uploads/");
  const isMicroscopyImage = isApiImage && imageSrc.includes("/images/");

  // Add backend URL for API and uploads paths
  if (isApiImage || isUploadsImage) {
    const apiUrl = process.env.NEXT_PUBLIC_API_URL || "";
    imageSrc = `${apiUrl}${imageSrc}`;

    // Add auth token for API images
    if (isApiImage && typeof window !== "undefined") {
      const token = localStorage.getItem("token");
      if (token) {
        const separator = imageSrc.includes("?") ? "&" : "?";
        imageSrc = `${imageSrc}${separator}token=${token}`;
      }
    }
  }

  return { url: imageSrc, isMicroscopy: isMicroscopyImage, isBase64: isBase64Image };
}
