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
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { motion, AnimatePresence } from "framer-motion";
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
  Users,
  Plus,
  LogOut,
  X,
  ChevronDown,
  ChevronUp,
  Crown,
  Pencil,
  UserMinus,
} from "lucide-react";
import { useAuthStore } from "@/stores/authStore";
import { useSettingsStore, DisplayMode, Theme, Language } from "@/stores/settingsStore";
import { api, GroupDetail, GroupMember } from "@/lib/api";
import { ConfirmModal } from "@/components/ui";

// Display mode visual configuration (labels come from translations)
const displayModeConfig: Record<DisplayMode, { bgColor: string; fgColor: string }> = {
  grayscale: { bgColor: "#000000", fgColor: "#ffffff" },
  inverted: { bgColor: "#ffffff", fgColor: "#000000" },
  green: { bgColor: "#000000", fgColor: "#00ff00" },
  fire: { bgColor: "#000000", fgColor: "#ff6600" },
};

const displayModeKeys: DisplayMode[] = ["grayscale", "inverted", "green", "fire"];

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

      {/* Group Section */}
      <GroupSection />
    </div>
  );
}

// =============================================================================
// Group Management Section
// =============================================================================

function GroupSection(): JSX.Element {
  const tg = useTranslations("groups");
  const tCommon = useTranslations("common");
  const queryClient = useQueryClient();
  const { user } = useAuthStore();

  // State
  const [showCreateDialog, setShowCreateDialog] = useState(false);
  const [showEditDialog, setShowEditDialog] = useState(false);
  const [showBrowseGroups, setShowBrowseGroups] = useState(false);
  const [confirmLeave, setConfirmLeave] = useState(false);
  const [confirmDelete, setConfirmDelete] = useState(false);
  const [confirmKickUserId, setConfirmKickUserId] = useState<number | null>(null);
  const [groupError, setGroupError] = useState<string | null>(null);
  const [groupSuccess, setGroupSuccess] = useState<string | null>(null);

  // Queries
  const { data: myGroupData, isLoading: isLoadingMyGroup } = useQuery({
    queryKey: ["myGroup"],
    queryFn: () => api.getMyGroup(),
  });

  const { data: allGroups, isLoading: isLoadingGroups } = useQuery({
    queryKey: ["groups"],
    queryFn: () => api.getGroups(),
    enabled: showBrowseGroups,
  });

  const myGroup = myGroupData?.group ?? null;
  const myMembership = myGroupData?.membership ?? null;
  const isCreator = myGroup && user && myGroup.created_by_user_id === user.id;

  // Clear success message after delay
  useEffect(() => {
    if (groupSuccess) {
      const timer = setTimeout(() => setGroupSuccess(null), 3000);
      return () => clearTimeout(timer);
    }
  }, [groupSuccess]);

  // Mutations
  const createGroupMutation = useMutation({
    mutationFn: (data: { name: string; description?: string }) => api.createGroup(data),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["myGroup"] });
      queryClient.invalidateQueries({ queryKey: ["groups"] });
      setShowCreateDialog(false);
      setGroupSuccess(tg("createSuccess"));
      setGroupError(null);
    },
    onError: (err: Error) => {
      setGroupError(err.message);
    },
  });

  const joinGroupMutation = useMutation({
    mutationFn: (groupId: number) => api.joinGroup(groupId),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["myGroup"] });
      queryClient.invalidateQueries({ queryKey: ["groups"] });
      setShowBrowseGroups(false);
      setGroupSuccess(tg("joinSuccess"));
      setGroupError(null);
    },
    onError: (err: Error) => {
      setGroupError(err.message);
    },
  });

  const leaveGroupMutation = useMutation({
    mutationFn: (groupId: number) => api.leaveGroup(groupId),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["myGroup"] });
      queryClient.invalidateQueries({ queryKey: ["groups"] });
      setConfirmLeave(false);
      setGroupSuccess(tg("leaveSuccess"));
      setGroupError(null);
    },
    onError: (err: Error) => {
      setGroupError(err.message);
    },
  });

  const deleteGroupMutation = useMutation({
    mutationFn: (groupId: number) => api.deleteGroup(groupId),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["myGroup"] });
      queryClient.invalidateQueries({ queryKey: ["groups"] });
      setConfirmDelete(false);
      setGroupSuccess(tg("deleteSuccess"));
      setGroupError(null);
    },
    onError: (err: Error) => {
      setGroupError(err.message);
    },
  });

  const updateGroupMutation = useMutation({
    mutationFn: ({ groupId, data }: { groupId: number; data: { name?: string; description?: string } }) =>
      api.updateGroup(groupId, data),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["myGroup"] });
      queryClient.invalidateQueries({ queryKey: ["groups"] });
      setShowEditDialog(false);
      setGroupSuccess(tg("updateSuccess"));
      setGroupError(null);
    },
    onError: (err: Error) => {
      setGroupError(err.message);
    },
  });

  const kickMemberMutation = useMutation({
    mutationFn: ({ groupId, userId }: { groupId: number; userId: number }) =>
      api.kickMember(groupId, userId),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["myGroup"] });
      setConfirmKickUserId(null);
      setGroupSuccess(tg("kickSuccess"));
      setGroupError(null);
    },
    onError: (err: Error) => {
      setGroupError(err.message);
    },
  });

  return (
    <>
      <motion.section
        initial={{ opacity: 0, y: 20 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ delay: 0.4 }}
        className="glass-card p-6 space-y-6"
      >
        <div className="flex items-center gap-3">
          <Users className="w-5 h-5 text-primary-400" />
          <h2 className="text-xl font-display font-semibold text-text-primary">{tg("title")}</h2>
        </div>

        {/* Error/Success Messages */}
        {groupError && (
          <div className="flex items-center gap-2 text-accent-red text-sm">
            <AlertCircle className="w-4 h-4" />
            {groupError}
            <button onClick={() => setGroupError(null)} className="ml-auto text-text-muted hover:text-text-primary">
              <X className="w-3 h-3" />
            </button>
          </div>
        )}
        {groupSuccess && (
          <div className="flex items-center gap-2 text-primary-400 text-sm">
            <Check className="w-4 h-4" />
            {groupSuccess}
          </div>
        )}

        {isLoadingMyGroup ? (
          <div className="flex justify-center py-6">
            <Loader2 className="w-6 h-6 text-primary-500 animate-spin" />
          </div>
        ) : myGroup ? (
          /* ---- User is in a group ---- */
          <div className="space-y-6">
            {/* Group info */}
            <div className="p-4 rounded-xl border border-white/10 bg-white/[0.02] space-y-3">
              <div className="flex items-center justify-between">
                <div>
                  <h3 className="text-lg font-display font-semibold text-text-primary">{myGroup.name}</h3>
                  {myGroup.description && (
                    <p className="text-sm text-text-secondary mt-1">{myGroup.description}</p>
                  )}
                </div>
                <div className="flex items-center gap-2">
                  {isCreator && (
                    <>
                      <button
                        onClick={() => setShowEditDialog(true)}
                        className="p-2 hover:bg-white/5 rounded-lg transition-colors text-text-muted hover:text-primary-400"
                        title={tg("editGroup")}
                      >
                        <Pencil className="w-4 h-4" />
                      </button>
                      <button
                        onClick={() => setConfirmDelete(true)}
                        className="p-2 hover:bg-accent-red/10 rounded-lg transition-colors text-text-muted hover:text-accent-red"
                        title={tg("deleteGroup")}
                      >
                        <Trash2 className="w-4 h-4" />
                      </button>
                    </>
                  )}
                </div>
              </div>
              <div className="flex items-center gap-4 text-sm text-text-muted">
                <span className="flex items-center gap-1.5">
                  <Users className="w-3.5 h-3.5" />
                  {tg("memberCount", { count: myGroup.member_count })}
                </span>
                <span className="flex items-center gap-1.5">
                  <Crown className="w-3.5 h-3.5" />
                  {tg("createdBy", { name: myGroup.creator_name })}
                </span>
              </div>
            </div>

            {/* Member list */}
            <div className="space-y-3">
              <h4 className="text-sm font-medium text-text-secondary">{tg("members")}</h4>
              <div className="space-y-2">
                {myGroup.members.map((member) => (
                  <div
                    key={member.id}
                    className="flex items-center justify-between p-3 rounded-lg border border-white/5 bg-white/[0.01]"
                  >
                    <div className="flex items-center gap-3">
                      <div className="w-8 h-8 rounded-full bg-primary-500/20 flex items-center justify-center">
                        <User className="w-4 h-4 text-primary-400" />
                      </div>
                      <div>
                        <div className="flex items-center gap-2">
                          <span className="text-sm font-medium text-text-primary">{member.user_name}</span>
                          {member.role === "creator" && (
                            <span className="px-1.5 py-0.5 text-xs rounded bg-primary-500/20 text-primary-400">
                              {tg("creator")}
                            </span>
                          )}
                          {member.role === "member" && (
                            <span className="px-1.5 py-0.5 text-xs rounded bg-white/5 text-text-muted">
                              {tg("member")}
                            </span>
                          )}
                        </div>
                        <span className="text-xs text-text-muted">{member.user_email}</span>
                      </div>
                    </div>
                    <div className="flex items-center gap-3">
                      <span className="text-xs text-text-muted">
                        {new Date(member.joined_at).toLocaleDateString()}
                      </span>
                      {isCreator && member.user_id !== user?.id && (
                        <button
                          onClick={() => setConfirmKickUserId(member.user_id)}
                          className="p-1.5 hover:bg-accent-red/10 rounded-lg transition-colors text-text-muted hover:text-accent-red"
                          title={tg("kickMember")}
                        >
                          <UserMinus className="w-3.5 h-3.5" />
                        </button>
                      )}
                    </div>
                  </div>
                ))}
              </div>
            </div>

            {/* Leave button */}
            {!isCreator && (
              <button
                onClick={() => setConfirmLeave(true)}
                className="flex items-center gap-2 text-sm text-accent-red hover:text-accent-red/80 transition-colors"
              >
                <LogOut className="w-4 h-4" />
                {tg("leaveGroup")}
              </button>
            )}
          </div>
        ) : (
          /* ---- User is not in a group ---- */
          <div className="space-y-6">
            <div className="text-center py-4">
              <div className="w-14 h-14 bg-primary-500/10 rounded-2xl flex items-center justify-center mx-auto mb-4">
                <Users className="w-7 h-7 text-primary-400" />
              </div>
              <p className="text-text-secondary mb-1">{tg("noGroup")}</p>
              <p className="text-sm text-text-muted">{tg("noGroupDesc")}</p>
            </div>

            <div className="flex flex-col sm:flex-row gap-3">
              <button
                onClick={() => setShowCreateDialog(true)}
                className="btn-primary flex-1 flex items-center justify-center gap-2"
              >
                <Plus className="w-4 h-4" />
                {tg("createGroup")}
              </button>
              <button
                onClick={() => setShowBrowseGroups(!showBrowseGroups)}
                className="btn-secondary flex-1 flex items-center justify-center gap-2"
              >
                {showBrowseGroups ? <ChevronUp className="w-4 h-4" /> : <ChevronDown className="w-4 h-4" />}
                {tg("browseGroups")}
              </button>
            </div>

            {/* Browse Groups Expandable */}
            <AnimatePresence>
              {showBrowseGroups && (
                <motion.div
                  initial={{ height: 0, opacity: 0 }}
                  animate={{ height: "auto", opacity: 1 }}
                  exit={{ height: 0, opacity: 0 }}
                  className="overflow-hidden"
                >
                  <div className="space-y-2 pt-2">
                    {isLoadingGroups ? (
                      <div className="flex justify-center py-4">
                        <Loader2 className="w-5 h-5 text-primary-500 animate-spin" />
                      </div>
                    ) : allGroups && allGroups.length > 0 ? (
                      allGroups.map((group) => (
                        <div
                          key={group.id}
                          className="flex items-center justify-between p-4 rounded-xl border border-white/10 bg-white/[0.02]"
                        >
                          <div>
                            <h4 className="text-sm font-medium text-text-primary">{group.name}</h4>
                            {group.description && (
                              <p className="text-xs text-text-muted mt-0.5">{group.description}</p>
                            )}
                            <div className="flex items-center gap-3 mt-1 text-xs text-text-muted">
                              <span>{tg("memberCount", { count: group.member_count })}</span>
                              <span>{tg("createdBy", { name: group.creator_name })}</span>
                            </div>
                          </div>
                          <button
                            onClick={() => joinGroupMutation.mutate(group.id)}
                            disabled={joinGroupMutation.isPending}
                            className="btn-primary text-sm px-4 py-1.5"
                          >
                            {joinGroupMutation.isPending ? (
                              <Loader2 className="w-3.5 h-3.5 animate-spin" />
                            ) : (
                              tg("joinGroup")
                            )}
                          </button>
                        </div>
                      ))
                    ) : (
                      <p className="text-sm text-text-muted text-center py-4">{tg("noGroupsAvailable")}</p>
                    )}
                  </div>
                </motion.div>
              )}
            </AnimatePresence>
          </div>
        )}
      </motion.section>

      {/* Create Group Dialog */}
      <AnimatePresence>
        {showCreateDialog && (
          <CreateGroupDialog
            onClose={() => setShowCreateDialog(false)}
            onSubmit={(data) => createGroupMutation.mutate(data)}
            isPending={createGroupMutation.isPending}
          />
        )}
      </AnimatePresence>

      {/* Edit Group Dialog */}
      <AnimatePresence>
        {showEditDialog && myGroup && (
          <EditGroupDialog
            group={myGroup}
            onClose={() => setShowEditDialog(false)}
            onSubmit={(data) =>
              updateGroupMutation.mutate({ groupId: myGroup.id, data })
            }
            isPending={updateGroupMutation.isPending}
          />
        )}
      </AnimatePresence>

      {/* Confirm Leave Modal */}
      <ConfirmModal
        isOpen={confirmLeave}
        onClose={() => setConfirmLeave(false)}
        onConfirm={() => myGroup && leaveGroupMutation.mutate(myGroup.id)}
        title={tg("leaveGroup")}
        message={tg("confirmLeave")}
        confirmLabel={tg("leaveGroup")}
        cancelLabel={tCommon("cancel")}
        isLoading={leaveGroupMutation.isPending}
        variant="danger"
      />

      {/* Confirm Delete Modal */}
      <ConfirmModal
        isOpen={confirmDelete}
        onClose={() => setConfirmDelete(false)}
        onConfirm={() => myGroup && deleteGroupMutation.mutate(myGroup.id)}
        title={tg("deleteGroup")}
        message={tg("confirmDelete")}
        confirmLabel={tg("deleteGroup")}
        cancelLabel={tCommon("cancel")}
        isLoading={deleteGroupMutation.isPending}
        variant="danger"
      />

      {/* Confirm Kick Modal */}
      <ConfirmModal
        isOpen={confirmKickUserId !== null}
        onClose={() => setConfirmKickUserId(null)}
        onConfirm={() =>
          myGroup &&
          confirmKickUserId !== null &&
          kickMemberMutation.mutate({ groupId: myGroup.id, userId: confirmKickUserId })
        }
        title={tg("kickMember")}
        message={tg("confirmKick")}
        confirmLabel={tg("kickMember")}
        cancelLabel={tCommon("cancel")}
        isLoading={kickMemberMutation.isPending}
        variant="danger"
      />
    </>
  );
}

// =============================================================================
// Create Group Dialog
// =============================================================================

function CreateGroupDialog({
  onClose,
  onSubmit,
  isPending,
}: {
  onClose: () => void;
  onSubmit: (data: { name: string; description?: string }) => void;
  isPending: boolean;
}): JSX.Element {
  const tg = useTranslations("groups");
  const tCommon = useTranslations("common");
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    onSubmit({ name, description: description || undefined });
  };

  return (
    <motion.div
      initial={{ opacity: 0 }}
      animate={{ opacity: 1 }}
      exit={{ opacity: 0 }}
      className="fixed inset-0 bg-black/50 backdrop-blur-sm flex items-center justify-center z-[100] p-4"
      onClick={onClose}
    >
      <motion.div
        initial={{ scale: 0.95, opacity: 0 }}
        animate={{ scale: 1, opacity: 1 }}
        exit={{ scale: 0.95, opacity: 0 }}
        className="glass-card p-6 w-full max-w-md"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-center justify-between mb-6">
          <h3 className="text-lg font-display font-semibold text-text-primary">{tg("createGroup")}</h3>
          <button onClick={onClose} className="p-2 hover:bg-white/5 rounded-lg transition-colors">
            <X className="w-5 h-5 text-text-muted" />
          </button>
        </div>

        <form onSubmit={handleSubmit} className="space-y-4">
          <div>
            <label className="block text-sm font-medium text-text-secondary mb-2">{tg("groupName")}</label>
            <input
              type="text"
              value={name}
              onChange={(e) => setName(e.target.value)}
              className="input-field"
              required
              autoFocus
            />
          </div>

          <div>
            <label className="block text-sm font-medium text-text-secondary mb-2">{tg("groupDescription")}</label>
            <textarea
              value={description}
              onChange={(e) => setDescription(e.target.value)}
              className="input-field min-h-[80px] resize-none"
            />
          </div>

          <div className="flex gap-3 pt-4">
            <button type="button" onClick={onClose} className="btn-secondary flex-1">
              {tCommon("cancel")}
            </button>
            <button
              type="submit"
              disabled={isPending || !name.trim()}
              className="btn-primary flex-1 flex items-center justify-center gap-2"
            >
              {isPending ? (
                <>
                  <Loader2 className="w-4 h-4 animate-spin" />
                  {tg("creating")}
                </>
              ) : (
                tg("createGroup")
              )}
            </button>
          </div>
        </form>
      </motion.div>
    </motion.div>
  );
}

// =============================================================================
// Edit Group Dialog
// =============================================================================

function EditGroupDialog({
  group,
  onClose,
  onSubmit,
  isPending,
}: {
  group: GroupDetail;
  onClose: () => void;
  onSubmit: (data: { name?: string; description?: string }) => void;
  isPending: boolean;
}): JSX.Element {
  const tg = useTranslations("groups");
  const tCommon = useTranslations("common");
  const [name, setName] = useState(group.name);
  const [description, setDescription] = useState(group.description || "");

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    const updates: { name?: string; description?: string } = {};
    if (name !== group.name) updates.name = name;
    if (description !== (group.description || "")) updates.description = description;
    if (Object.keys(updates).length > 0) {
      onSubmit(updates);
    }
  };

  return (
    <motion.div
      initial={{ opacity: 0 }}
      animate={{ opacity: 1 }}
      exit={{ opacity: 0 }}
      className="fixed inset-0 bg-black/50 backdrop-blur-sm flex items-center justify-center z-[100] p-4"
      onClick={onClose}
    >
      <motion.div
        initial={{ scale: 0.95, opacity: 0 }}
        animate={{ scale: 1, opacity: 1 }}
        exit={{ scale: 0.95, opacity: 0 }}
        className="glass-card p-6 w-full max-w-md"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-center justify-between mb-6">
          <h3 className="text-lg font-display font-semibold text-text-primary">{tg("editGroup")}</h3>
          <button onClick={onClose} className="p-2 hover:bg-white/5 rounded-lg transition-colors">
            <X className="w-5 h-5 text-text-muted" />
          </button>
        </div>

        <form onSubmit={handleSubmit} className="space-y-4">
          <div>
            <label className="block text-sm font-medium text-text-secondary mb-2">{tg("groupName")}</label>
            <input
              type="text"
              value={name}
              onChange={(e) => setName(e.target.value)}
              className="input-field"
              required
              autoFocus
            />
          </div>

          <div>
            <label className="block text-sm font-medium text-text-secondary mb-2">{tg("groupDescription")}</label>
            <textarea
              value={description}
              onChange={(e) => setDescription(e.target.value)}
              className="input-field min-h-[80px] resize-none"
            />
          </div>

          <div className="flex gap-3 pt-4">
            <button type="button" onClick={onClose} className="btn-secondary flex-1">
              {tCommon("cancel")}
            </button>
            <button
              type="submit"
              disabled={isPending || !name.trim()}
              className="btn-primary flex-1 flex items-center justify-center gap-2"
            >
              {isPending ? (
                <>
                  <Loader2 className="w-4 h-4 animate-spin" />
                  {tg("saving")}
                </>
              ) : (
                tCommon("save")
              )}
            </button>
          </div>
        </form>
      </motion.div>
    </motion.div>
  );
}
