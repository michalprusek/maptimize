"use client";

/**
 * useLocalStorage Hook
 *
 * Generic hook for persisting state to localStorage with type safety.
 * Handles SSR, validation, and error handling.
 */

import { useState, useEffect, useCallback, useRef } from "react";

/**
 * Options for useLocalStorage hook.
 */
interface UseLocalStorageOptions<T> {
  /** Validation function - return true if value is valid */
  validate?: (value: unknown) => value is T;
  /** Whether to sync across tabs (default: false) */
  syncAcrossTabs?: boolean;
}

/**
 * Generic localStorage hook with type safety and validation.
 *
 * @param key - localStorage key
 * @param defaultValue - Default value when nothing stored or validation fails
 * @param options - Optional validation and sync settings
 * @returns [value, setValue] tuple
 */
export function useLocalStorage<T>(
  key: string,
  defaultValue: T,
  options: UseLocalStorageOptions<T> = {}
): [T, (value: T | ((prev: T) => T)) => void] {
  const { validate, syncAcrossTabs = false } = options;

  // Initialize state from localStorage or default
  const [value, setValue] = useState<T>(() => {
    if (typeof window === "undefined") return defaultValue;

    try {
      const stored = localStorage.getItem(key);
      if (stored === null) return defaultValue;

      const parsed = JSON.parse(stored);
      if (validate && !validate(parsed)) {
        return defaultValue;
      }
      return parsed as T;
    } catch {
      return defaultValue;
    }
  });

  // Track if we've initialized (for SSR hydration)
  const isInitialized = useRef(false);

  // Persist to localStorage when value changes
  useEffect(() => {
    if (typeof window === "undefined") return;

    // Skip the first render to avoid overwriting during hydration
    if (!isInitialized.current) {
      isInitialized.current = true;
      return;
    }

    try {
      localStorage.setItem(key, JSON.stringify(value));
    } catch (error) {
      console.error(`[useLocalStorage] Failed to save ${key}:`, error);
    }
  }, [key, value]);

  // Listen for storage events from other tabs
  useEffect(() => {
    if (!syncAcrossTabs || typeof window === "undefined") return;

    function handleStorageChange(e: StorageEvent): void {
      if (e.key !== key) return;

      try {
        if (e.newValue === null) {
          setValue(defaultValue);
          return;
        }

        const parsed = JSON.parse(e.newValue);
        if (validate && !validate(parsed)) {
          return;
        }
        setValue(parsed as T);
      } catch {
        // Ignore invalid JSON from other tabs
      }
    }

    window.addEventListener("storage", handleStorageChange);
    return () => window.removeEventListener("storage", handleStorageChange);
  }, [key, defaultValue, validate, syncAcrossTabs]);

  return [value, setValue];
}

/**
 * Lightweight version for syncing external state to localStorage.
 * Use when you already have state from another source (e.g., parent component).
 *
 * @param key - localStorage key
 * @param currentValue - Current value to sync
 * @param setValue - Setter to call when restoring from localStorage
 * @param validate - Optional validation function
 */
export function useSyncToLocalStorage<T>(
  key: string,
  currentValue: T,
  setValue: (value: T) => void,
  validate?: (value: unknown) => value is T
): void {
  const isInitialized = useRef(false);

  // Restore from localStorage on mount
  useEffect(() => {
    if (isInitialized.current) return;

    try {
      const stored = localStorage.getItem(key);
      if (stored !== null) {
        const parsed = JSON.parse(stored);
        if (!validate || validate(parsed)) {
          setValue(parsed as T);
        }
      }
    } catch (error) {
      console.error(`[useSyncToLocalStorage] Failed to load ${key}:`, error);
    }

    isInitialized.current = true;
  }, [key, setValue, validate]);

  // Save to localStorage when value changes (after initialization)
  useEffect(() => {
    if (!isInitialized.current) return;

    try {
      localStorage.setItem(key, JSON.stringify(currentValue));
    } catch (error) {
      console.error(`[useSyncToLocalStorage] Failed to save ${key}:`, error);
    }
  }, [key, currentValue]);
}
