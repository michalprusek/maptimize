"use client";

/**
 * Settings Page
 *
 * Allows users to manage:
 * - Profile (avatar, name, email, password)
 * - Appearance (theme, display mode/LUT)
 * - Language preferences
 */

import { useState, useRef, useEffect } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { motion } from "framer-motion";
import { useTranslations } from "next-intl";
import {
  User,
  Camera,
  Trash2,
  Loader2,
  Check,
  Sun,
  Moon,
  Globe,
  Palette,
  Lock,
  AlertCircle,
  Eye,
  EyeOff,
} from "lucide-react";
import { useAuthStore } from "@/stores/authStore";
import { useSettingsStore, DisplayMode, Theme, Language } from "@/stores/settingsStore";
import { api } from "@/lib/api";

// Display mode visual configuration (labels come from translations)
const displayModeConfig: Record<DisplayMode, { bgColor: string; fgColor: string }> = {
  grayscale: { bgColor: "#000000", fgColor: "#ffffff" },
  inverted: { bgColor: "#ffffff", fgColor: "#000000" },
  green: { bgColor: "#000000", fgColor: "#00ff00" },
  fire: { bgColor: "#000000", fgColor: "#ff6600" },
  hilo: { bgColor: "#808080", fgColor: "linear-gradient(to right, #0000ff, #808080, #ff0000)" },
};

const displayModeKeys: DisplayMode[] = ["grayscale", "inverted", "green", "fire", "hilo"];

const languageOptions: { value: Language; flag: string }[] = [
  { value: "en", flag: "EN" },
  { value: "fr", flag: "FR" },
];

/** Get toggle button classes based on selection state */
function getToggleButtonClass(isSelected: boolean): string {
  const base = "flex items-center gap-3 px-4 py-3 rounded-xl border-2 transition-all";
  return isSelected
    ? `${base} border-primary-500 bg-primary-500/10`
    : `${base} border-white/10 hover:border-white/20`;
}

export default function SettingsPage(): JSX.Element {
  const queryClient = useQueryClient();
  const { user } = useAuthStore();
  const {
    displayMode,
    theme,
    language,
    setDisplayMode,
    setTheme,
    setLanguage,
  } = useSettingsStore();

  // Translations
  const t = useTranslations("settings");
  const tCommon = useTranslations("common");

  // Profile form state
  const [name, setName] = useState(user?.name || "");
  const [email, setEmail] = useState(user?.email || "");
  const [profileError, setProfileError] = useState<string | null>(null);
  const [profileSuccess, setProfileSuccess] = useState(false);

  // Avatar error state
  const [avatarError, setAvatarError] = useState<string | null>(null);

  // Password form state
  const [currentPassword, setCurrentPassword] = useState("");
  const [newPassword, setNewPassword] = useState("");
  const [confirmPassword, setConfirmPassword] = useState("");
  const [showCurrentPassword, setShowCurrentPassword] = useState(false);
  const [showNewPassword, setShowNewPassword] = useState(false);
  const [passwordError, setPasswordError] = useState<string | null>(null);
  const [passwordSuccess, setPasswordSuccess] = useState(false);

  // Avatar file input ref
  const fileInputRef = useRef<HTMLInputElement>(null);

  // Update local state when user changes
  useEffect(() => {
    if (user) {
      setName(user.name);
      setEmail(user.email);
    }
  }, [user]);

  // Avatar mutations
  const uploadAvatarMutation = useMutation({
    mutationFn: (file: File) => api.uploadAvatar(file),
    onSuccess: () => {
      setAvatarError(null);
      queryClient.invalidateQueries({ queryKey: ["user"] });
      // Refresh auth state to get new avatar URL
      useAuthStore.getState().checkAuth();
    },
    onError: (err: Error) => {
      setAvatarError(err.message || t("profile.avatarUploadError"));
    },
  });

  const deleteAvatarMutation = useMutation({
    mutationFn: () => api.deleteAvatar(),
    onSuccess: () => {
      setAvatarError(null);
      queryClient.invalidateQueries({ queryKey: ["user"] });
      useAuthStore.getState().checkAuth();
    },
    onError: (err: Error) => {
      setAvatarError(err.message || t("profile.avatarDeleteError"));
    },
  });

  // Profile update mutation
  const updateProfileMutation = useMutation({
    mutationFn: (data: { name?: string; email?: string }) => api.updateProfile(data),
    onSuccess: () => {
      setProfileSuccess(true);
      setProfileError(null);
      useAuthStore.getState().checkAuth();
      setTimeout(() => setProfileSuccess(false), 3000);
    },
    onError: (err: Error) => {
      setProfileError(err.message);
      setProfileSuccess(false);
    },
  });

  // Password change mutation
  const changePasswordMutation = useMutation({
    mutationFn: (data: { current_password: string; new_password: string; confirm_password: string }) =>
      api.changePassword(data),
    onSuccess: () => {
      setPasswordSuccess(true);
      setPasswordError(null);
      setCurrentPassword("");
      setNewPassword("");
      setConfirmPassword("");
      setTimeout(() => setPasswordSuccess(false), 3000);
    },
    onError: (err: Error) => {
      setPasswordError(err.message);
      setPasswordSuccess(false);
    },
  });

  const handleAvatarChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (file) {
      uploadAvatarMutation.mutate(file);
    }
  };

  const handleProfileSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    const updates: { name?: string; email?: string } = {};
    if (name !== user?.name) updates.name = name;
    if (email !== user?.email) updates.email = email;

    if (Object.keys(updates).length > 0) {
      updateProfileMutation.mutate(updates);
    }
  };

  const handlePasswordSubmit = (e: React.FormEvent) => {
    e.preventDefault();

    if (newPassword !== confirmPassword) {
      setPasswordError(t("password.mismatch"));
      return;
    }

    if (newPassword.length < 8) {
      setPasswordError(t("password.tooShort"));
      return;
    }

    changePasswordMutation.mutate({
      current_password: currentPassword,
      new_password: newPassword,
      confirm_password: confirmPassword,
    });
  };

  return (
    <div className="max-w-4xl mx-auto space-y-8">
      {/* Header */}
      <div>
        <h1 className="text-3xl font-display font-bold text-text-primary">{t("title")}</h1>
      </div>

      {/* Profile Section */}
      <motion.section
        initial={{ opacity: 0, y: 20 }}
        animate={{ opacity: 1, y: 0 }}
        className="glass-card p-6 space-y-6"
      >
        <div className="flex items-center gap-3">
          <User className="w-5 h-5 text-primary-400" />
          <h2 className="text-xl font-display font-semibold text-text-primary">{t("profile.title")}</h2>
        </div>

        {/* Avatar */}
        <div className="flex items-center gap-6">
          <div className="relative">
            <div className="w-24 h-24 rounded-full bg-primary-500/20 flex items-center justify-center overflow-hidden">
              {user?.avatar_url && api.getAvatarUrl(user.avatar_url) ? (
                <img
                  src={api.getAvatarUrl(user.avatar_url)}
                  alt={t("profile.avatarAlt")}
                  className="w-full h-full object-cover"
                  onError={(e) => {
                    // Hide broken image and let parent show fallback
                    (e.target as HTMLImageElement).style.display = 'none';
                  }}
                />
              ) : (
                <User className="w-12 h-12 text-primary-400" />
              )}
            </div>
            <button
              onClick={() => fileInputRef.current?.click()}
              disabled={uploadAvatarMutation.isPending}
              className="absolute bottom-0 right-0 p-2 bg-primary-500 rounded-full hover:bg-primary-400 transition-colors disabled:opacity-50"
            >
              {uploadAvatarMutation.isPending ? (
                <Loader2 className="w-4 h-4 text-white animate-spin" />
              ) : (
                <Camera className="w-4 h-4 text-white" />
              )}
            </button>
          </div>

          <input
            ref={fileInputRef}
            type="file"
            accept="image/*"
            onChange={handleAvatarChange}
            className="hidden"
          />

          <div className="space-y-2">
            <p className="text-sm text-text-secondary">
              {t("profile.uploadAvatar")}
            </p>
            {user?.avatar_url && api.getAvatarUrl(user.avatar_url) && (
              <button
                onClick={() => deleteAvatarMutation.mutate()}
                disabled={deleteAvatarMutation.isPending}
                className="flex items-center gap-2 text-sm text-accent-red hover:text-accent-red/80"
              >
                <Trash2 className="w-4 h-4" />
                {t("profile.removeAvatar")}
              </button>
            )}
            {avatarError && (
              <div className="flex items-center gap-2 text-accent-red text-sm">
                <AlertCircle className="w-4 h-4" />
                {avatarError}
              </div>
            )}
          </div>
        </div>

        {/* Profile Form */}
        <form onSubmit={handleProfileSubmit} className="space-y-4">
          <div>
            <label className="block text-sm font-medium text-text-secondary mb-2">
              {t("profile.name")}
            </label>
            <input
              type="text"
              value={name}
              onChange={(e) => setName(e.target.value)}
              className="input-field"
            />
          </div>

          <div>
            <label className="block text-sm font-medium text-text-secondary mb-2">
              {t("profile.email")}
            </label>
            <input
              type="email"
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              className="input-field"
            />
          </div>

          {profileError && (
            <div className="flex items-center gap-2 text-accent-red text-sm">
              <AlertCircle className="w-4 h-4" />
              {profileError}
            </div>
          )}

          {profileSuccess && (
            <div className="flex items-center gap-2 text-primary-400 text-sm">
              <Check className="w-4 h-4" />
              {tCommon("success")}
            </div>
          )}

          <button
            type="submit"
            disabled={updateProfileMutation.isPending}
            className="btn-primary"
          >
            {updateProfileMutation.isPending ? (
              <>
                <Loader2 className="w-4 h-4 animate-spin mr-2" />
                {tCommon("loading")}
              </>
            ) : (
              t("profile.updateProfile")
            )}
          </button>
        </form>
      </motion.section>

      {/* Password Section */}
      <motion.section
        initial={{ opacity: 0, y: 20 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ delay: 0.1 }}
        className="glass-card p-6 space-y-6"
      >
        <div className="flex items-center gap-3">
          <Lock className="w-5 h-5 text-primary-400" />
          <h2 className="text-xl font-display font-semibold text-text-primary">{t("password.title")}</h2>
        </div>

        <form onSubmit={handlePasswordSubmit} className="space-y-4">
          <div>
            <label className="block text-sm font-medium text-text-secondary mb-2">
              {t("password.current")}
            </label>
            <div className="relative">
              <input
                type={showCurrentPassword ? "text" : "password"}
                value={currentPassword}
                onChange={(e) => setCurrentPassword(e.target.value)}
                className="input-field pr-10"
              />
              <button
                type="button"
                onClick={() => setShowCurrentPassword(!showCurrentPassword)}
                className="absolute right-3 top-1/2 -translate-y-1/2 text-text-muted hover:text-text-secondary"
              >
                {showCurrentPassword ? <EyeOff className="w-4 h-4" /> : <Eye className="w-4 h-4" />}
              </button>
            </div>
          </div>

          <div>
            <label className="block text-sm font-medium text-text-secondary mb-2">
              {t("password.new")}
            </label>
            <div className="relative">
              <input
                type={showNewPassword ? "text" : "password"}
                value={newPassword}
                onChange={(e) => setNewPassword(e.target.value)}
                className="input-field pr-10"
              />
              <button
                type="button"
                onClick={() => setShowNewPassword(!showNewPassword)}
                className="absolute right-3 top-1/2 -translate-y-1/2 text-text-muted hover:text-text-secondary"
              >
                {showNewPassword ? <EyeOff className="w-4 h-4" /> : <Eye className="w-4 h-4" />}
              </button>
            </div>
          </div>

          <div>
            <label className="block text-sm font-medium text-text-secondary mb-2">
              {t("password.confirm")}
            </label>
            <input
              type="password"
              value={confirmPassword}
              onChange={(e) => setConfirmPassword(e.target.value)}
              className="input-field"
            />
          </div>

          {passwordError && (
            <div className="flex items-center gap-2 text-accent-red text-sm">
              <AlertCircle className="w-4 h-4" />
              {passwordError}
            </div>
          )}

          {passwordSuccess && (
            <div className="flex items-center gap-2 text-primary-400 text-sm">
              <Check className="w-4 h-4" />
              {t("password.changed")}
            </div>
          )}

          <button
            type="submit"
            disabled={changePasswordMutation.isPending || !currentPassword || !newPassword || !confirmPassword}
            className="btn-primary"
          >
            {changePasswordMutation.isPending ? (
              <>
                <Loader2 className="w-4 h-4 animate-spin mr-2" />
                {tCommon("loading")}
              </>
            ) : (
              t("password.change")
            )}
          </button>
        </form>
      </motion.section>

      {/* Appearance Section */}
      <motion.section
        initial={{ opacity: 0, y: 20 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ delay: 0.2 }}
        className="glass-card p-6 space-y-6"
      >
        <div className="flex items-center gap-3">
          <Palette className="w-5 h-5 text-primary-400" />
          <h2 className="text-xl font-display font-semibold text-text-primary">{t("appearance.title")}</h2>
        </div>

        {/* Theme Toggle */}
        <div className="space-y-3">
          <label className="block text-sm font-medium text-text-secondary">{t("appearance.theme")}</label>
          <div className="flex gap-4">
            <button
              onClick={() => setTheme("dark")}
              className={getToggleButtonClass(theme === "dark")}
            >
              <Moon className="w-5 h-5" />
              <span>{t("appearance.darkMode")}</span>
              {theme === "dark" && <Check className="w-4 h-4 text-primary-400" />}
            </button>

            <button
              onClick={() => setTheme("light")}
              className={getToggleButtonClass(theme === "light")}
            >
              <Sun className="w-5 h-5" />
              <span>{t("appearance.lightMode")}</span>
              {theme === "light" && <Check className="w-4 h-4 text-primary-400" />}
            </button>
          </div>
        </div>

        {/* Display Mode (LUT) */}
        <div className="space-y-3">
          <label className="block text-sm font-medium text-text-secondary">
            {t("display.mode")}
          </label>
          <div className="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-5 gap-3">
            {displayModeKeys.map((mode) => {
              const isSelected = displayMode === mode;
              const config = displayModeConfig[mode];
              return (
                <button
                  key={mode}
                  onClick={() => setDisplayMode(mode)}
                  className={`p-4 rounded-xl border-2 transition-all text-left ${
                    isSelected
                      ? "border-primary-500 bg-primary-500/10"
                      : "border-white/10 hover:border-white/20"
                  }`}
                >
                  <div
                    className="w-full aspect-square rounded-lg mb-3 flex items-center justify-center"
                    style={{ backgroundColor: config.bgColor }}
                  >
                    <div
                      className="w-8 h-8 rounded-full"
                      style={{ background: config.fgColor }}
                    />
                  </div>
                  <p className="font-medium text-text-primary text-sm">{t(`display.${mode}`)}</p>
                  <p className="text-xs text-text-muted">{t(`display.${mode}Desc`)}</p>
                  {isSelected && <Check className="w-4 h-4 text-primary-400 mt-2" />}
                </button>
              );
            })}
          </div>
        </div>
      </motion.section>

      {/* Language Section */}
      <motion.section
        initial={{ opacity: 0, y: 20 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ delay: 0.3 }}
        className="glass-card p-6 space-y-6"
      >
        <div className="flex items-center gap-3">
          <Globe className="w-5 h-5 text-primary-400" />
          <h2 className="text-xl font-display font-semibold text-text-primary">{t("language.title")}</h2>
        </div>

        <div className="flex gap-4">
          {languageOptions.map((lang) => (
            <button
              key={lang.value}
              onClick={() => setLanguage(lang.value)}
              className={getToggleButtonClass(language === lang.value)}
            >
              <span className="font-mono font-bold text-primary-400">{lang.flag}</span>
              <span>{t(`language.${lang.value}`)}</span>
              {language === lang.value && <Check className="w-4 h-4 text-primary-400" />}
            </button>
          ))}
        </div>
      </motion.section>
    </div>
  );
}
