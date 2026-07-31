"use client";

import { useCallback, useState } from "react";
import { useTranslations } from "next-intl";
import { AlertCircle, Check, Copy, Plug, Terminal } from "lucide-react";

/**
 * ConnectAssistantPanel
 *
 * Read-only "connect an AI assistant" panel. The connector implements the MCP
 * authorization spec rather than any one vendor's protocol, so the URL below is
 * the whole configuration for every client that speaks it — the differences are
 * only where each app hides its "add a connector" button. Sign-in runs through
 * OAuth against the MAPtimize account, so there is no token to generate, list or
 * revoke.
 *
 * Self-contained, so it can be dropped into the documents-page modal or the
 * settings-page section alike.
 */
const MCP_CONNECTOR_URL = "https://maptimize.utia.cas.cz/mcp/";
const CLI_COMMAND =
  "claude mcp add --transport http maptalk https://maptimize.utia.cas.cz/mcp/";

/** Small copy-to-clipboard button with a transient "copied" tick. */
function CopyButton({ value, label }: { value: string; label: string }) {
  const tCommon = useTranslations("common");
  const t = useTranslations("connectAssistant");
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

/** One client's setup steps. Every entry is a peer -- no client is "the" client. */
function ClientCard({
  icon: Icon,
  title,
  steps,
  command,
  commandLabel,
}: {
  icon: typeof Plug;
  title: string;
  steps: string;
  command?: string;
  commandLabel?: string;
}) {
  return (
    <div className="flex items-start gap-3 px-4 py-3 rounded-lg bg-white/[0.03] border border-white/10">
      <div className="p-2 rounded-lg bg-primary-500/10 flex-shrink-0">
        <Icon className="w-4 h-4 text-primary-400" />
      </div>
      <div className="min-w-0 flex-1 space-y-2">
        <h4 className="text-sm font-medium text-text-primary">{title}</h4>
        <p className="text-xs text-text-muted leading-relaxed">{steps}</p>
        {command && (
          <div className="flex items-center gap-2">
            <code className="flex-1 min-w-0 overflow-x-auto whitespace-nowrap px-3 py-2 rounded-lg bg-white/5 border border-white/10 text-xs font-mono text-primary-400">
              {command}
            </code>
            <CopyButton value={command} label={commandLabel ?? ""} />
          </div>
        )}
      </div>
    </div>
  );
}

export function ConnectAssistantPanel() {
  const t = useTranslations("connectAssistant");

  return (
    <div className="space-y-5">
      {/* The one thing every client needs. */}
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
        <p className="text-xs text-text-muted leading-relaxed">{t("urlHint")}</p>
      </div>

      <ClientCard icon={Plug} title={t("claudeTitle")} steps={t("claudeSteps")} />
      <ClientCard icon={Plug} title={t("chatgptTitle")} steps={t("chatgptSteps")} />
      <ClientCard
        icon={Terminal}
        title={t("cliTitle")}
        steps={t("cliSteps")}
        command={CLI_COMMAND}
        commandLabel={t("copyCommand")}
      />
    </div>
  );
}
