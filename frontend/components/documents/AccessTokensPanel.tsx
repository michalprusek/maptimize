"use client";

import { useCallback, useEffect, useState } from "react";
import { useTranslations } from "next-intl";
import { motion, AnimatePresence } from "framer-motion";
import {
  Key,
  Plus,
  Copy,
  Check,
  Trash2,
  Loader2,
  AlertTriangle,
  AlertCircle,
  X,
} from "lucide-react";
import { clsx } from "clsx";
import { formatDistanceToNow } from "date-fns";
import { api, AccessToken, AccessTokenCreated } from "@/lib/api";
import { ConfirmModal } from "@/components/ui/ConfirmModal";

// The MCP endpoint the lab's reverse proxy exposes. Paired with a personal
// access token, this is what a user pastes into Claude Desktop's connector setup.
const MCP_CONNECTOR_URL = "https://maptimize.utia.cas.cz/mcp/";

/** Small copy-to-clipboard button with a transient "copied" tick. */
function CopyButton({ value, label }: { value: string; label: string }) {
  const tCommon = useTranslations("common");
  const t = useTranslations("accessTokens");
  const [copied, setCopied] = useState(false);
  const [failed, setFailed] = useState(false);

  const copy = useCallback(async () => {
    try {
      await navigator.clipboard.writeText(value);
      setFailed(false);
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    } catch {
      setFailed(true);
      setTimeout(() => setFailed(false), 2000);
    }
  }, [value]);

  return (
    <button
      onClick={copy}
      className="flex items-center gap-1.5 px-2.5 py-1.5 rounded-lg bg-white/5 hover:bg-white/10 text-text-secondary hover:text-text-primary text-xs font-medium transition-colors"
      title={label}
    >
      {copied ? (
        <>
          <Check className="w-3.5 h-3.5 text-green-400" />
          {t("copied")}
        </>
      ) : failed ? (
        <>
          <AlertCircle className="w-3.5 h-3.5 text-red-400" />
          {tCommon("copyFailed")}
        </>
      ) : (
        <>
          <Copy className="w-3.5 h-3.5" />
          {t("copy")}
        </>
      )}
    </button>
  );
}

/**
 * AccessTokensPanel
 *
 * Manages a user's personal MCP access tokens ("Connect to Claude"): lists them,
 * generates new ones (revealing the plaintext exactly once), and revokes them.
 * Self-contained (no react-query) so it can be dropped into the documents page
 * modal or the settings page section alike.
 */
export function AccessTokensPanel() {
  const t = useTranslations("accessTokens");
  const tCommon = useTranslations("common");

  const [tokens, setTokens] = useState<AccessToken[]>([]);
  const [isLoading, setIsLoading] = useState(true);
  const [loadError, setLoadError] = useState<string | null>(null);

  const [showCreateDialog, setShowCreateDialog] = useState(false);
  const [newLabel, setNewLabel] = useState("");
  const [isCreating, setIsCreating] = useState(false);
  const [createError, setCreateError] = useState<string | null>(null);

  // The one-time plaintext reveal of a freshly created token.
  const [revealed, setRevealed] = useState<AccessTokenCreated | null>(null);

  const [tokenToRevoke, setTokenToRevoke] = useState<AccessToken | null>(null);
  const [isRevoking, setIsRevoking] = useState(false);

  const loadTokens = useCallback(async () => {
    setIsLoading(true);
    try {
      const list = await api.listAccessTokens();
      setTokens(list);
      setLoadError(null);
    } catch (error) {
      setLoadError(error instanceof Error ? error.message : t("loadError"));
    } finally {
      setIsLoading(false);
    }
  }, [t]);

  useEffect(() => {
    loadTokens();
  }, [loadTokens]);

  const handleCreate = async () => {
    if (!newLabel.trim()) return;
    setIsCreating(true);
    setCreateError(null);
    try {
      const created = await api.createAccessToken(newLabel.trim());
      setRevealed(created);
      setShowCreateDialog(false);
      setNewLabel("");
      await loadTokens();
    } catch (error) {
      setCreateError(error instanceof Error ? error.message : t("createError"));
    } finally {
      setIsCreating(false);
    }
  };

  const handleRevoke = async () => {
    if (!tokenToRevoke) return;
    setIsRevoking(true);
    try {
      await api.revokeAccessToken(tokenToRevoke.id);
      setTokenToRevoke(null);
      await loadTokens();
    } catch (error) {
      setLoadError(error instanceof Error ? error.message : t("revokeError"));
    } finally {
      setIsRevoking(false);
    }
  };

  const activeTokens = tokens.filter((tok) => !tok.revoked_at);

  return (
    <div className="space-y-5">
      {/* Connector URL */}
      <div className="space-y-2">
        <label className="block text-sm font-medium text-text-secondary">
          {t("connectorUrl")}
        </label>
        <div className="flex items-center gap-2">
          <code className="flex-1 min-w-0 truncate px-3 py-2 rounded-lg bg-white/5 border border-white/10 text-sm font-mono text-primary-400">
            {MCP_CONNECTOR_URL}
          </code>
          <CopyButton value={MCP_CONNECTOR_URL} label={t("copyUrl")} />
        </div>
        <p className="text-xs text-text-muted">{t("setupHint")}</p>
      </div>

      {/* Token list header */}
      <div className="flex items-center justify-between">
        <h3 className="text-sm font-medium text-text-secondary">{t("tokensTitle")}</h3>
        <button
          onClick={() => {
            setCreateError(null);
            setNewLabel("");
            setShowCreateDialog(true);
          }}
          className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg bg-primary-500/15 hover:bg-primary-500/25 border border-primary-500/20 text-primary-400 text-sm font-medium transition-colors"
        >
          <Plus className="w-4 h-4" />
          {t("generate")}
        </button>
      </div>

      {/* Errors */}
      {loadError && (
        <div className="flex items-center gap-2 text-red-400 text-sm">
          <AlertCircle className="w-4 h-4 flex-shrink-0" />
          {loadError}
        </div>
      )}

      {/* Token list */}
      {isLoading ? (
        <div className="flex justify-center py-6">
          <Loader2 className="w-5 h-5 text-primary-500 animate-spin" />
        </div>
      ) : activeTokens.length === 0 ? (
        <div className="text-center py-6 text-sm text-text-muted">{t("noTokens")}</div>
      ) : (
        <div className="space-y-2">
          {activeTokens.map((tok) => (
            <div
              key={tok.id}
              className="flex items-center gap-3 px-4 py-3 rounded-lg bg-white/[0.03] border border-white/10"
            >
              <div className="p-2 rounded-lg bg-primary-500/10 flex-shrink-0">
                <Key className="w-4 h-4 text-primary-400" />
              </div>
              <div className="flex-1 min-w-0">
                <div className="flex items-center gap-2 min-w-0">
                  <span className="truncate text-sm font-medium text-text-primary">{tok.label}</span>
                  <code className="flex-shrink-0 px-1.5 py-0.5 rounded text-[10px] font-mono bg-white/5 text-text-muted">
                    {tok.token_prefix}…
                  </code>
                </div>
                <div className="text-xs text-text-muted mt-0.5">
                  {t("created")} {formatDistanceToNow(new Date(tok.created_at), { addSuffix: true })}
                  {" • "}
                  {t("lastUsed")}{" "}
                  {tok.last_used_at
                    ? formatDistanceToNow(new Date(tok.last_used_at), { addSuffix: true })
                    : t("never")}
                </div>
              </div>
              <button
                onClick={() => setTokenToRevoke(tok)}
                className="p-2 hover:bg-red-500/20 rounded-lg text-text-muted hover:text-red-400 transition-colors flex-shrink-0"
                title={t("revoke")}
              >
                <Trash2 className="w-4 h-4" />
              </button>
            </div>
          ))}
        </div>
      )}

      {/* Generate dialog */}
      <AnimatePresence>
        {showCreateDialog && (
          <motion.div
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            exit={{ opacity: 0 }}
            className="fixed inset-0 z-[120] bg-black/60 backdrop-blur-sm flex items-center justify-center p-4"
            onClick={() => !isCreating && setShowCreateDialog(false)}
          >
            <motion.div
              initial={{ scale: 0.95, opacity: 0 }}
              animate={{ scale: 1, opacity: 1 }}
              exit={{ scale: 0.95, opacity: 0 }}
              className="w-full max-w-md bg-bg-secondary rounded-xl border border-white/10 shadow-2xl p-6 space-y-4"
              onClick={(e) => e.stopPropagation()}
            >
              <div className="flex items-center justify-between">
                <h3 className="text-lg font-semibold text-text-primary">{t("generate")}</h3>
                <button
                  onClick={() => setShowCreateDialog(false)}
                  className="p-1.5 rounded-lg hover:bg-white/10 text-text-secondary hover:text-text-primary transition-colors"
                >
                  <X className="w-5 h-5" />
                </button>
              </div>
              <div>
                <label className="block text-sm font-medium text-text-secondary mb-2">
                  {t("labelName")}
                </label>
                <input
                  type="text"
                  value={newLabel}
                  onChange={(e) => setNewLabel(e.target.value)}
                  onKeyDown={(e) => {
                    if (e.key === "Enter" && !isCreating) handleCreate();
                  }}
                  placeholder={t("labelPlaceholder")}
                  autoFocus
                  className="input-field"
                />
              </div>
              {createError && (
                <div className="flex items-center gap-2 text-red-400 text-sm">
                  <AlertCircle className="w-4 h-4 flex-shrink-0" />
                  {createError}
                </div>
              )}
              <div className="flex gap-3 pt-2">
                <button
                  onClick={() => setShowCreateDialog(false)}
                  className="btn-secondary flex-1"
                >
                  {tCommon("cancel")}
                </button>
                <button
                  onClick={handleCreate}
                  disabled={isCreating || !newLabel.trim()}
                  className="btn-primary flex-1 flex items-center justify-center gap-2"
                >
                  {isCreating && <Loader2 className="w-4 h-4 animate-spin" />}
                  {isCreating ? t("generating") : t("create")}
                </button>
              </div>
            </motion.div>
          </motion.div>
        )}
      </AnimatePresence>

      {/* One-time token reveal */}
      <AnimatePresence>
        {revealed && (
          <motion.div
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            exit={{ opacity: 0 }}
            className="fixed inset-0 z-[120] bg-black/60 backdrop-blur-sm flex items-center justify-center p-4"
          >
            <motion.div
              initial={{ scale: 0.95, opacity: 0 }}
              animate={{ scale: 1, opacity: 1 }}
              exit={{ scale: 0.95, opacity: 0 }}
              className="w-full max-w-md bg-bg-secondary rounded-xl border border-white/10 shadow-2xl p-6 space-y-4"
            >
              <div className="flex items-center gap-2">
                <Key className="w-5 h-5 text-primary-400" />
                <h3 className="text-lg font-semibold text-text-primary">{t("revealTitle")}</h3>
              </div>
              <div className="flex items-start gap-2 px-3 py-2.5 rounded-lg bg-amber-500/10 border border-amber-500/20">
                <AlertTriangle className="w-4 h-4 text-amber-400 flex-shrink-0 mt-0.5" />
                <p className="text-xs text-amber-400/90">{t("revealWarning")}</p>
              </div>
              <div className="space-y-2">
                <code className="block w-full break-all px-3 py-2.5 rounded-lg bg-white/5 border border-white/10 text-sm font-mono text-primary-400">
                  {revealed.token}
                </code>
                <div className="flex justify-end">
                  <CopyButton value={revealed.token} label={t("copyToken")} />
                </div>
              </div>
              <button
                onClick={() => setRevealed(null)}
                className="btn-primary w-full"
              >
                {t("done")}
              </button>
            </motion.div>
          </motion.div>
        )}
      </AnimatePresence>

      {/* Revoke confirmation */}
      <ConfirmModal
        isOpen={tokenToRevoke !== null}
        onClose={() => setTokenToRevoke(null)}
        onConfirm={handleRevoke}
        title={t("revokeTitle")}
        message={t("revokeWarning")}
        detail={tokenToRevoke?.label}
        confirmLabel={t("revoke")}
        cancelLabel={tCommon("cancel")}
        isLoading={isRevoking}
        variant="danger"
      />
    </div>
  );
}
