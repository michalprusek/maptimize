"use client";

import { useState, useMemo } from "react";
import { useTranslations } from "next-intl";
import { useDocumentStore } from "@/stores/documentStore";
import type { DiscoveredPaper } from "@/lib/api";
import { X, Search, ExternalLink, Loader2, Lock, Check } from "lucide-react";
import { clsx } from "clsx";

interface DiscoverSourcesModalProps {
  isOpen: boolean;
  onClose: () => void;
}

// Selectability rule shared between the "select all" memo and each row's
// checkbox/disabled state -- keep it in one place so they can never drift.
function isSelectable(p: DiscoveredPaper): boolean {
  return Boolean(p.importable && !p.already_imported && p.doi);
}

export function DiscoverSourcesModal({ isOpen, onClose }: DiscoverSourcesModalProps) {
  const t = useTranslations("documents");
  const tCommon = useTranslations("common");
  const {
    discoverResults,
    discoverEffectiveQuery, discoverRewriteFailed, isDiscovering, isImportingPapers,
    discoverSources, importDiscovered,
  } = useDocumentStore();

  const [query, setQuery] = useState("");
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [summary, setSummary] = useState<string | null>(null);
  // Distinguishes "searched and found nothing" from "search errored out" --
  // an empty discoverResults array means both, so it can't drive the UI alone.
  const [hasSearched, setHasSearched] = useState(false);
  const [searchFailed, setSearchFailed] = useState(false);
  const [importFailed, setImportFailed] = useState(false);
  const [failedImports, setFailedImports] = useState<{ doi: string; reason: string }[]>([]);

  // Only open-access, not-yet-imported papers can be selected.
  const selectable = useMemo(
    () => discoverResults.filter(isSelectable),
    [discoverResults]
  );
  const allSelected = selectable.length > 0 && selected.size === selectable.length;

  const runSearch = async () => {
    if (!query.trim()) return;
    setSelected(new Set());
    setSummary(null);
    setSearchFailed(false);
    setImportFailed(false);
    setFailedImports([]);
    try {
      await discoverSources(query.trim());
      setHasSearched(true);
    } catch (error) {
      setHasSearched(true);
      setSearchFailed(true);
      setSummary(error instanceof Error ? error.message : t("discoverFailed"));
    }
  };

  const toggle = (doi: string) => {
    setSelected((prev) => {
      const next = new Set(prev);
      next.has(doi) ? next.delete(doi) : next.add(doi);
      return next;
    });
  };

  const toggleAll = () => {
    setSelected((prev) =>
      prev.size === selectable.length
        ? new Set()
        : new Set(selectable.map((p) => p.doi as string))
    );
  };

  const runImport = async () => {
    setSummary(null);
    setImportFailed(false);
    setFailedImports([]);
    try {
      const result = await importDiscovered(Array.from(selected));
      const duplicates = result.already_in_library?.length ?? 0;
      setSummary(
        `${t("discoverImportedCount", { count: result.imported })}` +
          // Duplicates are neither imported nor failed -- reported on their own
          // so "0 imported" doesn't read as an error when everything selected
          // was simply already here.
          (duplicates ? ` · ${t("importAlreadyInLibrary", { count: duplicates })}` : "") +
          (result.failed.length
            ? ` · ${t("discoverFailedCount", { count: result.failed.length })}`
            : "")
      );
      setFailedImports(result.failed);
      setSelected(new Set());
    } catch (error) {
      setImportFailed(true);
      setSummary(error instanceof Error ? error.message : t("discoverImportFailed"));
    }
  };

  if (!isOpen) return null;

  return (
    <>
      <div className="fixed inset-0 z-[110] bg-black/60 backdrop-blur-sm animate-fade-in" onClick={onClose} />
      <div className="fixed inset-0 z-[111] flex items-center justify-center p-4 pointer-events-none">
        <div
          className={clsx(
            "w-full max-w-2xl max-h-[85vh] bg-bg-secondary rounded-xl border border-white/10",
            "shadow-2xl pointer-events-auto flex flex-col animate-scale-in"
          )}
          onClick={(e) => e.stopPropagation()}
        >
          <div className="flex items-center justify-between px-5 py-4 border-b border-white/10">
            <h2 className="text-lg font-semibold text-text-primary">{t("discoverSources")}</h2>
            <button onClick={onClose} className="p-1.5 rounded-lg hover:bg-white/10 text-text-secondary hover:text-text-primary transition-colors">
              <X className="w-5 h-5" />
            </button>
          </div>

          <div className="px-5 py-4 border-b border-white/10 flex gap-2">
            <input
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              onKeyDown={(e) => { if (e.key === "Enter" && !isDiscovering) runSearch(); }}
              placeholder={t("discoverPlaceholder")}
              className="flex-1 px-3 py-2 text-sm bg-white/5 border border-white/10 rounded-lg text-text-primary placeholder:text-text-muted focus:outline-none focus:border-primary-500/50"
            />
            <button
              onClick={runSearch}
              disabled={isDiscovering || !query.trim()}
              className="px-4 py-2 rounded-lg bg-primary-500/20 hover:bg-primary-500/30 border border-primary-500/30 text-primary-400 text-sm font-medium disabled:opacity-50 flex items-center gap-2"
            >
              {isDiscovering ? <Loader2 className="w-4 h-4 animate-spin" /> : <Search className="w-4 h-4" />}
              {isDiscovering ? t("discoverSearching") : t("discoverSearch")}
            </button>
          </div>

          {/* Kept outside the scrollable results pane (directly under the search
              input) so it stays visible while scrolling results -- its job is to
              let the user spot a bad translation, which they can't do if it has
              scrolled out of view. */}
          {!isDiscovering && (discoverEffectiveQuery || discoverRewriteFailed) && (
            <div className="px-5 py-2 border-b border-white/10 space-y-1">
              {discoverEffectiveQuery && (
                <div className="text-xs text-text-secondary">
                  <span className="text-text-muted">{t("discoverSearchedAs")}</span>{" "}
                  <code className="px-1.5 py-0.5 rounded bg-white/[0.06] text-primary-400 font-mono font-medium break-all">
                    {discoverEffectiveQuery}
                  </code>
                </div>
              )}
              {discoverRewriteFailed && (
                <div className="text-xs text-amber-400/90">{t("discoverRewriteFailed")}</div>
              )}
            </div>
          )}

          <div className="flex-1 overflow-y-auto p-5 space-y-2">
            {searchFailed && !isDiscovering && (
              <div className="text-center py-8 text-red-400">{summary || t("discoverFailed")}</div>
            )}
            {!searchFailed && hasSearched && discoverResults.length === 0 && !isDiscovering && (
              <div className="text-center py-8 text-text-muted">{t("discoverNoResults")}</div>
            )}
            {discoverResults.map((p) => {
              const disabled = !isSelectable(p);
              return (
                <label
                  key={p.doi || p.source_url}
                  className={clsx(
                    "flex items-start gap-3 p-3 rounded-lg border transition-colors",
                    disabled
                      ? "border-white/5 bg-white/[0.01] opacity-60 cursor-default"
                      : "border-white/10 hover:bg-white/5 cursor-pointer"
                  )}
                >
                  <input
                    type="checkbox"
                    disabled={disabled}
                    checked={!!p.doi && selected.has(p.doi)}
                    onChange={() => p.doi && toggle(p.doi)}
                    className="mt-1 w-4 h-4 rounded border-white/20 bg-bg-secondary text-primary-500 focus:ring-primary-500 disabled:opacity-40"
                  />
                  <span className="flex-1 min-w-0">
                    <span className="block text-sm font-medium text-text-primary">{p.title}</span>
                    <span className="block text-xs text-text-muted mt-0.5">
                      {[p.authors, p.journal, p.year].filter(Boolean).join(" · ")}
                    </span>
                    {p.abstract && (
                      <span className="block text-xs text-text-secondary mt-1 line-clamp-2">{p.abstract}</span>
                    )}
                    <span className="flex items-center gap-2 mt-2">
                      {p.already_imported ? (
                        <span className="inline-flex items-center gap-1 px-1.5 py-0.5 rounded text-[10px] bg-green-500/15 text-green-400 border border-green-500/20">
                          <Check className="w-3 h-3" />{t("discoverAlreadyImported")}
                        </span>
                      ) : p.importable ? (
                        <span className="px-1.5 py-0.5 rounded text-[10px] bg-primary-500/15 text-primary-400 border border-primary-500/20">
                          {t("discoverOpenAccess")}
                        </span>
                      ) : (
                        <span className="inline-flex items-center gap-1 px-1.5 py-0.5 rounded text-[10px] bg-amber-500/15 text-amber-400 border border-amber-500/20">
                          <Lock className="w-3 h-3" />{t("discoverPaywalled")}
                        </span>
                      )}
                      <a
                        href={p.source_url}
                        target="_blank"
                        rel="noopener noreferrer"
                        onClick={(e) => e.stopPropagation()}
                        className="inline-flex items-center gap-1 text-[10px] text-text-muted hover:text-primary-400"
                      >
                        <ExternalLink className="w-3 h-3" />{t("discoverOpenPublisher")}
                      </a>
                    </span>
                  </span>
                </label>
              );
            })}
          </div>

          <div className="px-5 py-4 border-t border-white/10 space-y-2">
            <div className="flex items-center justify-between">
              <div className="flex items-center gap-3">
                <button
                  onClick={toggleAll}
                  disabled={selectable.length === 0}
                  className="text-xs text-text-secondary hover:text-text-primary disabled:opacity-40"
                >
                  {allSelected ? tCommon("deselect") : t("discoverSelectAll")}
                </button>
                {summary && (
                  <span className={clsx("text-xs", (searchFailed || importFailed) ? "text-red-400" : "text-text-muted")}>
                    {summary}
                  </span>
                )}
              </div>
              <div className="flex items-center gap-2">
                <button onClick={onClose} className="px-3 py-2 rounded-lg text-sm text-text-secondary hover:bg-white/5">
                  {tCommon("cancel")}
                </button>
                <button
                  onClick={runImport}
                  disabled={selected.size === 0 || isImportingPapers}
                  className="px-4 py-2 rounded-lg bg-primary-500/20 hover:bg-primary-500/30 border border-primary-500/30 text-primary-400 text-sm font-medium disabled:opacity-50 flex items-center gap-2"
                >
                  {isImportingPapers && <Loader2 className="w-4 h-4 animate-spin" />}
                  {isImportingPapers ? t("discoverImporting") : `${t("discoverImportSelected")} (${selected.size})`}
                </button>
              </div>
            </div>
            {failedImports.length > 0 && (
              <ul className="max-h-24 overflow-y-auto space-y-0.5">
                {failedImports.map((f) => (
                  <li
                    key={f.doi}
                    className="text-[10px] text-red-400/90 font-mono truncate"
                    title={`${f.doi}: ${f.reason}`}
                  >
                    {f.doi} — {f.reason}
                  </li>
                ))}
              </ul>
            )}
          </div>
        </div>
      </div>
    </>
  );
}
