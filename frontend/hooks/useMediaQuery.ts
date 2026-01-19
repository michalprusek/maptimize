"use client";

/**
 * Media Query Hooks
 *
 * Custom hooks for responsive design and user preferences.
 */

import { useState, useEffect } from "react";

// Common breakpoints
export const BREAKPOINTS = {
  mobile: 640,
  tablet: 1024,
  desktop: 1280,
} as const;

/**
 * Hook that returns true when viewport width is >= breakpoint.
 * Uses debounced resize handling for performance.
 *
 * @param breakpoint - Minimum width in pixels
 * @returns Boolean indicating if viewport matches
 */
export function useMediaQuery(breakpoint: number): boolean {
  const [matches, setMatches] = useState(false);

  useEffect(() => {
    const checkMatch = () => setMatches(window.innerWidth >= breakpoint);
    checkMatch();

    let timeoutId: NodeJS.Timeout;
    const handleResize = () => {
      clearTimeout(timeoutId);
      timeoutId = setTimeout(checkMatch, 100);
    };

    window.addEventListener("resize", handleResize);
    return () => {
      window.removeEventListener("resize", handleResize);
      clearTimeout(timeoutId);
    };
  }, [breakpoint]);

  return matches;
}

/**
 * Hook that detects user's reduced motion preference.
 * Respects the prefers-reduced-motion media query.
 *
 * @returns Boolean indicating if user prefers reduced motion
 */
export function useReducedMotion(): boolean {
  const [reducedMotion, setReducedMotion] = useState(false);

  useEffect(() => {
    const mediaQuery = window.matchMedia("(prefers-reduced-motion: reduce)");
    setReducedMotion(mediaQuery.matches);

    const handler = (e: MediaQueryListEvent) => setReducedMotion(e.matches);
    mediaQuery.addEventListener("change", handler);
    return () => mediaQuery.removeEventListener("change", handler);
  }, []);

  return reducedMotion;
}
