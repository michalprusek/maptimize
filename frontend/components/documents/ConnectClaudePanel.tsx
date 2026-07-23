"use client";

import { useCallback, useState } from "react";
import { useTranslations } from "next-intl";
import { Copy, Check, AlertCircle, Plug, Terminal } from "lucide-react";

// The MCP endpoint the lab's reverse proxy exposes. Authentication now happens
// through OAuth in Claude Desktop / Claude Code, so this URL is all a user needs
// to paste — no personal access token to generate.
const MCP_CONNECTOR_URL = "https://maptimize.utia.cas.cz/mcp/";
const CLAUDE_CODE_COMMAND =
  "claude mcp add --transport http maptalk https://maptimize.utia.cas.cz/mcp/";

/** Small copy-to-clipboard button with a transient "copied" tick. */
function CopyButton({ value, label }: { value: string; label: string }) {
  const tCommon = useTranslations("common");
  const t = useTranslations("connectClaude");
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
      className="flex items-center gap-1.5 px-2.5 py-1.5 rounded-lg bg-white/5 hover:bg-white/10 text-text-secondary hover:text-text-primary text-xs font-medium transition-colors flex-shrink-0"
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
 * ConnectClaudePanel
 *
 * Read-only "Connect to Claude" info panel. Authentication runs through OAuth
 * (both Claude Desktop and Claude Code sign in with the MAPtimize account), so
 * the only thing a user needs is the connector URL plus setup steps — there is
 * no token to generate, list, or revoke. Self-contained so it can be dropped
 * into the documents-page modal or the settings-page section alike.
 */
export function ConnectClaudePanel() {
  const t = useTranslations("connectClaude");

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
      </div>

      {/* Claude Desktop */}
      <div className="flex items-start gap-3 px-4 py-3 rounded-lg bg-white/[0.03] border border-white/10">
        <div className="p-2 rounded-lg bg-primary-500/10 flex-shrink-0">
          <Plug className="w-4 h-4 text-primary-400" />
        </div>
        <div className="min-w-0 space-y-1">
          <h4 className="text-sm font-medium text-text-primary">{t("desktopTitle")}</h4>
          <p className="text-xs text-text-muted leading-relaxed">{t("desktopSteps")}</p>
        </div>
      </div>

      {/* Claude Code */}
      <div className="flex items-start gap-3 px-4 py-3 rounded-lg bg-white/[0.03] border border-white/10">
        <div className="p-2 rounded-lg bg-primary-500/10 flex-shrink-0">
          <Terminal className="w-4 h-4 text-primary-400" />
        </div>
        <div className="min-w-0 flex-1 space-y-2">
          <h4 className="text-sm font-medium text-text-primary">{t("codeTitle")}</h4>
          <p className="text-xs text-text-muted leading-relaxed">{t("codeSteps")}</p>
          <div className="flex items-center gap-2">
            <code className="flex-1 min-w-0 overflow-x-auto whitespace-nowrap px-3 py-2 rounded-lg bg-white/5 border border-white/10 text-xs font-mono text-primary-400">
              {CLAUDE_CODE_COMMAND}
            </code>
            <CopyButton value={CLAUDE_CODE_COMMAND} label={t("copyCommand")} />
          </div>
        </div>
      </div>
    </div>
  );
}
