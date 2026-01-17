/**
 * useReducedMotion Hook
 *
 * Respects the user's "prefers-reduced-motion" accessibility preference.
 * When enabled, animations should be simplified or disabled.
 *
 * Usage:
 * ```tsx
 * const prefersReducedMotion = useReducedMotion();
 * // Then conditionally apply animations
 * ```
 *
 * @see https://developer.mozilla.org/en-US/docs/Web/CSS/@media/prefers-reduced-motion
 */

import { useEffect, useState } from "react";

const QUERY = "(prefers-reduced-motion: reduce)";

/**
 * Returns true if the user prefers reduced motion (accessibility setting).
 * Updates automatically when the preference changes.
 */
export function useReducedMotion(): boolean {
  // Default to false during SSR, will sync on client
  const [prefersReducedMotion, setPrefersReducedMotion] = useState(false);

  useEffect(() => {
    const mediaQuery = window.matchMedia(QUERY);

    // Set initial value
    setPrefersReducedMotion(mediaQuery.matches);

    // Listen for changes
    const handleChange = (event: MediaQueryListEvent) => {
      setPrefersReducedMotion(event.matches);
    };

    // Modern browsers use addEventListener
    mediaQuery.addEventListener("change", handleChange);

    return () => {
      mediaQuery.removeEventListener("change", handleChange);
    };
  }, []);

  return prefersReducedMotion;
}

/**
 * Returns animation variants that respect reduced motion preferences.
 * Returns empty/static variants when user prefers reduced motion.
 */
export function useAnimationVariants<T extends Record<string, unknown>>(
  variants: T,
  reducedVariants?: T
): T {
  const prefersReducedMotion = useReducedMotion();

  if (prefersReducedMotion && reducedVariants) {
    return reducedVariants;
  }

  return variants;
}
