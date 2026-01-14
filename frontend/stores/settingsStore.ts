/**
 * Settings store for user display preferences.
 *
 * Manages:
 * - Display mode (LUT): grayscale, inverted, green, fire, hilo
 * - Theme: dark, light
 * - Language: en, fr
 *
 * Settings are persisted locally and synced with the backend.
 */

import { create } from "zustand";
import { persist } from "zustand/middleware";
import { api, DisplayMode, Theme, Language } from "@/lib/api";

interface SettingsState {
  // State
  displayMode: DisplayMode;
  theme: Theme;
  language: Language;
  isLoading: boolean;
  isSyncing: boolean;

  // Actions
  setDisplayMode: (mode: DisplayMode) => void;
  setTheme: (theme: Theme) => void;
  setLanguage: (language: Language) => void;
  loadSettings: () => Promise<void>;
  syncSettings: () => Promise<void>;
}

// Apply theme to document
function applyTheme(theme: Theme) {
  if (typeof document === "undefined") return;

  const root = document.documentElement;
  if (theme === "light") {
    root.classList.remove("dark");
    root.classList.add("light");
  } else {
    root.classList.remove("light");
    root.classList.add("dark");
  }
}

export const useSettingsStore = create<SettingsState>()(
  persist(
    (set, get) => ({
      // Default state
      displayMode: "grayscale",
      theme: "dark",
      language: "en",
      isLoading: false,
      isSyncing: false,

      setDisplayMode: (mode) => {
        set({ displayMode: mode });
        get().syncSettings();
      },

      setTheme: (theme) => {
        set({ theme });
        applyTheme(theme);
        get().syncSettings();
      },

      setLanguage: (language) => {
        // Save scroll position before language change triggers re-render
        // This will be restored by IntlClientProvider after remount
        if (typeof window !== "undefined") {
          (window as unknown as { __savedScrollY?: number }).__savedScrollY = window.scrollY;
        }
        set({ language });
        get().syncSettings();
      },

      loadSettings: async () => {
        set({ isLoading: true });
        try {
          const settings = await api.getSettings();
          set({
            displayMode: settings.display_mode,
            theme: settings.theme,
            language: settings.language,
          });
          applyTheme(settings.theme);
        } catch (error) {
          console.error("Failed to load settings:", error);
          // Keep local settings on error
        } finally {
          set({ isLoading: false });
        }
      },

      syncSettings: async () => {
        const { displayMode, theme, language, isSyncing } = get();

        // Debounce: skip if already syncing
        if (isSyncing) return;

        set({ isSyncing: true });
        try {
          await api.updateSettings({
            display_mode: displayMode,
            theme: theme,
            language: language,
          });
        } catch (error) {
          console.error("Failed to sync settings:", error);
          // Settings are still persisted locally
        } finally {
          set({ isSyncing: false });
        }
      },
    }),
    {
      name: "maptimize-settings",
      partialize: (state) => ({
        displayMode: state.displayMode,
        theme: state.theme,
        language: state.language,
      }),
      onRehydrateStorage: () => {
        // Apply theme after rehydration
        return (state) => {
          if (state) {
            applyTheme(state.theme);
          }
        };
      },
    }
  )
);

// Types re-exported from api.ts for convenience
export type { DisplayMode, Theme, Language } from "@/lib/api";
