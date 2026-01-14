"use client";

import { useState, useEffect } from "react";
import { useTranslations } from "next-intl";
import { Bug, Loader2, ChevronDown, ChevronUp, Check } from "lucide-react";
import { Dialog } from "./Dialog";
import { api, BugReportCategory } from "@/lib/api";
import { useAuthStore } from "@/stores/authStore";
import { useSettingsStore } from "@/stores/settingsStore";

interface BugReportModalProps {
  isOpen: boolean;
  onClose: () => void;
}

interface DebugInfo {
  browser: string;
  screenSize: string;
  pageUrl: string;
  userSettings: string;
}

function collectDebugInfo(): DebugInfo {
  const settings = useSettingsStore.getState();

  return {
    browser: typeof navigator !== "undefined" ? navigator.userAgent : "Unknown",
    screenSize:
      typeof window !== "undefined"
        ? `${window.innerWidth}x${window.innerHeight}`
        : "Unknown",
    pageUrl: typeof window !== "undefined" ? window.location.href : "Unknown",
    userSettings: JSON.stringify({
      display_mode: settings.displayMode,
      theme: settings.theme,
      language: settings.language,
    }),
  };
}

export function BugReportModal({
  isOpen,
  onClose,
}: BugReportModalProps): JSX.Element {
  const t = useTranslations("bugReport");
  const tCommon = useTranslations("common");
  const { user } = useAuthStore();

  const [description, setDescription] = useState("");
  const [category, setCategory] = useState<BugReportCategory>("bug");
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [showDebugInfo, setShowDebugInfo] = useState(false);
  const [debugInfo, setDebugInfo] = useState<DebugInfo | null>(null);
  const [success, setSuccess] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (isOpen) {
      setDebugInfo(collectDebugInfo());
      setDescription("");
      setCategory("bug");
      setSuccess(false);
      setError(null);
    }
  }, [isOpen]);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!description.trim() || description.length < 10) return;

    setIsSubmitting(true);
    setError(null);

    try {
      await api.submitBugReport({
        description: description.trim(),
        category,
        browser_info: debugInfo?.browser,
        page_url: debugInfo?.pageUrl,
        screen_resolution: debugInfo?.screenSize,
        user_settings_json: debugInfo?.userSettings,
      });

      setSuccess(true);
      setTimeout(() => {
        onClose();
      }, 1500);
    } catch (err) {
      setError(err instanceof Error ? err.message : t("error"));
    } finally {
      setIsSubmitting(false);
    }
  };

  const categories: { value: BugReportCategory; label: string }[] = [
    { value: "bug", label: t("bug") },
    { value: "feature", label: t("feature") },
    { value: "other", label: t("other") },
  ];

  return (
    <Dialog
      isOpen={isOpen}
      onClose={onClose}
      title={t("title")}
      icon={<Bug className="w-5 h-5 text-accent-amber" />}
      maxWidth="md"
    >
      {success ? (
        <div className="flex flex-col items-center justify-center py-8">
          <div className="w-16 h-16 rounded-full bg-accent-green/20 flex items-center justify-center mb-4">
            <Check className="w-8 h-8 text-accent-green" />
          </div>
          <p className="text-text-primary font-medium">{t("success")}</p>
        </div>
      ) : (
        <form onSubmit={handleSubmit} className="space-y-4">
          {/* User info (read-only) */}
          <div className="flex items-center gap-3 p-3 bg-bg-secondary/50 rounded-lg">
            <div className="w-10 h-10 rounded-full bg-primary-500/20 flex items-center justify-center">
              <span className="text-primary-400 font-medium">
                {user?.name?.charAt(0).toUpperCase() || "?"}
              </span>
            </div>
            <div>
              <p className="text-sm font-medium text-text-primary">
                {user?.name}
              </p>
              <p className="text-xs text-text-muted">{user?.email}</p>
            </div>
          </div>

          {/* Category */}
          <div>
            <label className="block text-sm text-text-secondary mb-2">
              {t("category")}
            </label>
            <div className="flex gap-2">
              {categories.map((cat) => (
                <button
                  key={cat.value}
                  type="button"
                  onClick={() => setCategory(cat.value)}
                  className={`px-4 py-2 rounded-lg text-sm transition-all ${
                    category === cat.value
                      ? "bg-primary-500 text-white"
                      : "bg-bg-secondary text-text-secondary hover:bg-white/10"
                  }`}
                >
                  {cat.label}
                </button>
              ))}
            </div>
          </div>

          {/* Description */}
          <div>
            <label className="block text-sm text-text-secondary mb-1">
              {t("description")} <span className="text-accent-red">*</span>
            </label>
            <textarea
              value={description}
              onChange={(e) => setDescription(e.target.value)}
              placeholder={t("descriptionPlaceholder")}
              rows={4}
              className="w-full px-4 py-2 bg-bg-secondary border border-white/10 rounded-lg
                         focus:outline-none focus:border-primary-500 resize-none
                         text-text-primary placeholder:text-text-muted"
              required
              minLength={10}
              autoFocus
            />
            <p className="text-xs text-text-muted mt-1">
              {description.length}/5000
            </p>
          </div>

          {/* Debug info collapsible */}
          <div className="border border-white/10 rounded-lg overflow-hidden">
            <button
              type="button"
              onClick={() => setShowDebugInfo(!showDebugInfo)}
              className="w-full px-4 py-3 flex items-center justify-between
                         bg-bg-secondary/50 hover:bg-bg-secondary transition-colors"
            >
              <div>
                <span className="text-sm text-text-secondary">
                  {t("debugInfo")}
                </span>
                <p className="text-xs text-text-muted">{t("debugInfoDesc")}</p>
              </div>
              {showDebugInfo ? (
                <ChevronUp className="w-5 h-5 text-text-muted" />
              ) : (
                <ChevronDown className="w-5 h-5 text-text-muted" />
              )}
            </button>

            {showDebugInfo && debugInfo && (
              <div className="p-4 space-y-2 bg-bg-tertiary/30 text-xs font-mono">
                <div>
                  <span className="text-text-muted">{t("browser")}:</span>
                  <p className="text-text-secondary truncate">
                    {debugInfo.browser}
                  </p>
                </div>
                <div>
                  <span className="text-text-muted">{t("screenSize")}:</span>
                  <p className="text-text-secondary">{debugInfo.screenSize}</p>
                </div>
                <div>
                  <span className="text-text-muted">{t("currentPage")}:</span>
                  <p className="text-text-secondary truncate">
                    {debugInfo.pageUrl}
                  </p>
                </div>
                <div>
                  <span className="text-text-muted">{t("userSettings")}:</span>
                  <p className="text-text-secondary">
                    {debugInfo.userSettings}
                  </p>
                </div>
              </div>
            )}
          </div>

          {/* Error message */}
          {error && (
            <div className="p-3 bg-accent-red/10 border border-accent-red/20 rounded-lg">
              <p className="text-accent-red text-sm">{error}</p>
            </div>
          )}

          {/* Actions */}
          <div className="flex justify-end gap-3 pt-4 border-t border-white/5">
            <button
              type="button"
              onClick={onClose}
              className="px-4 py-2 text-text-secondary hover:text-text-primary transition-colors"
            >
              {tCommon("cancel")}
            </button>
            <button
              type="submit"
              disabled={
                isSubmitting || !description.trim() || description.length < 10
              }
              className="btn-primary flex items-center gap-2"
            >
              {isSubmitting ? (
                <>
                  <Loader2 className="w-4 h-4 animate-spin" />
                  {t("submitting")}
                </>
              ) : (
                <>
                  <Bug className="w-4 h-4" />
                  {t("submit")}
                </>
              )}
            </button>
          </div>
        </form>
      )}
    </Dialog>
  );
}
