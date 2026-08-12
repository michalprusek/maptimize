"use client";

/**
 * The folder sidebar on the experiments page.
 *
 * A shared tree, not a private one: whoever can see an experiment can file it,
 * the same rule as the microscope / PTM / protein assignments. Filing changes
 * where an experiment appears and nothing else — sharing is a separate,
 * owner-only decision, and this panel deliberately offers no control for it.
 *
 * "All" and "Unfiled" are not folders and never become folders. They are the two
 * views that exist before anyone makes one, and `Unfiled` is what stops a fresh
 * experiment from being invisible until someone files it.
 */
import { useMemo, useState } from "react";
import { useTranslations } from "next-intl";
import {
  ChevronDown,
  ChevronRight,
  Folder as FolderIcon,
  FolderOpen,
  FolderPlus,
  Inbox,
  Layers,
  Pencil,
  Trash2,
} from "lucide-react";

import type { ExperimentFolder } from "@/lib/api";
import { Dialog } from "@/components/ui";
import { childrenOf, subtreeTotal } from "@/lib/folderTree";

/** Selected view. A number is a folder id; the two strings are the fixed views. */
export type FolderSelection = number | "all" | "unfiled";

/**
 * A pending name entry. `folder` null means "create under `parentId`"; otherwise
 * it is a rename of that folder. One shape for both, because the dialog is the
 * same single text field either way.
 */
interface NameRequest {
  folder: ExperimentFolder | null;
  parentId: number | null;
}

interface Props {
  folders: ExperimentFolder[];
  selection: FolderSelection;
  onSelect: (selection: FolderSelection) => void;
  onCreate: (
    name: string,
    parentId: number | null,
    groupId: number | null
  ) => void;
  /**
   * Groups the caller belongs to. With two or more, a top-level folder has to
   * say which one it joins — the server will otherwise make it private, and this
   * tree seeds no group roots to nest under, so they could never start a shared
   * one.
   */
  groups: { id: number; name: string }[];
  onRename: (folder: ExperimentFolder, name: string) => void;
  onDelete: (folder: ExperimentFolder) => void;
  unfiledCount: number;
  totalCount: number;
}

function FolderRow({
  folder,
  folders,
  depth,
  selection,
  onSelect,
  onName,
  onDelete,
  t,
}: {
  folder: ExperimentFolder;
  folders: ExperimentFolder[];
  depth: number;
  selection: FolderSelection;
  onSelect: Props["onSelect"];
  onName: (request: NameRequest) => void;
  onDelete: Props["onDelete"];
  t: ReturnType<typeof useTranslations>;
}): JSX.Element {
  const [expanded, setExpanded] = useState(true);
  const children = useMemo(
    () => childrenOf(folders, folder.id),
    [folders, folder.id]
  );
  // The whole subtree, not just this folder: a parent reading "0" while its
  // children hold the batch is the thing a tree is supposed to prevent.
  const count = subtreeTotal(folders, folder.id, (f) => f.experiment_count);
  const active = selection === folder.id;

  return (
    <div>
      <div
        className={`group flex items-center gap-1 rounded-lg px-2 py-1.5 cursor-pointer transition-colors ${
          active ? "bg-primary-500/15 text-text-primary" : "hover:bg-white/5 text-text-secondary"
        }`}
        style={{ paddingLeft: `${depth * 14 + 8}px` }}
        onClick={() => onSelect(folder.id)}
      >
        <button
          type="button"
          className="p-0.5 shrink-0"
          onClick={(event) => {
            event.stopPropagation();
            setExpanded((open) => !open);
          }}
          aria-label={expanded ? t("collapse") : t("expand")}
        >
          {children.length > 0 ? (
            expanded ? (
              <ChevronDown className="w-3.5 h-3.5" />
            ) : (
              <ChevronRight className="w-3.5 h-3.5" />
            )
          ) : (
            <span className="block w-3.5" />
          )}
        </button>
        {active ? (
          <FolderOpen className="w-4 h-4 shrink-0 text-primary-400" />
        ) : (
          <FolderIcon className="w-4 h-4 shrink-0" />
        )}
        <span className="truncate flex-1 text-sm">{folder.name}</span>
        <span className="text-xs text-text-muted tabular-nums">{count}</span>

        <div className="flex items-center opacity-0 group-hover:opacity-100 transition-opacity">
          <button
            type="button"
            className="p-1 hover:text-primary-400"
            title={t("newSubfolder")}
            onClick={(event) => {
              event.stopPropagation();
              onName({ folder: null, parentId: folder.id });
            }}
          >
            <FolderPlus className="w-3.5 h-3.5" />
          </button>
          <button
            type="button"
            className="p-1 hover:text-primary-400"
            title={t("rename")}
            onClick={(event) => {
              event.stopPropagation();
              onName({ folder, parentId: folder.parent_id });
            }}
          >
            <Pencil className="w-3.5 h-3.5" />
          </button>
          <button
            type="button"
            className="p-1 hover:text-accent-red"
            title={t("deleteFolder")}
            onClick={(event) => {
              event.stopPropagation();
              onDelete(folder);
            }}
          >
            <Trash2 className="w-3.5 h-3.5" />
          </button>
        </div>
      </div>

      {expanded &&
        children.map((child) => (
          <FolderRow
            key={child.id}
            folder={child}
            folders={folders}
            depth={depth + 1}
            selection={selection}
            onSelect={onSelect}
            onName={onName}
            onDelete={onDelete}
            t={t}
          />
        ))}
    </div>
  );
}

export function ExperimentFolderPanel({
  folders,
  groups,
  selection,
  onSelect,
  onCreate,
  onRename,
  onDelete,
  unfiledCount,
  totalCount,
}: Props): JSX.Element {
  const t = useTranslations("experimentFolders");
  const tCommon = useTranslations("common");
  const [naming, setNaming] = useState<NameRequest | null>(null);
  const [draftName, setDraftName] = useState("");
  const [draftGroup, setDraftGroup] = useState<number | null>(null);

  // Only ever asked for a NEW top-level folder: a subfolder inherits, and a
  // rename is not a move. With one group there is nothing to choose.
  const askForGroup =
    naming !== null &&
    naming.folder === null &&
    naming.parentId === null &&
    groups.length > 1;

  // Top level via the shared helper: a folder whose parent is invisible is
  // surfaced here rather than dropped, so nothing can hide behind an ACL edge.
  const roots = useMemo(() => childrenOf(folders, null), [folders]);

  const openNaming = (request: NameRequest) => {
    setDraftName(request.folder?.name ?? "");
    setDraftGroup(groups.length === 1 ? groups[0].id : null);
    setNaming(request);
  };

  const submitName = (event: React.FormEvent) => {
    event.preventDefault();
    const name = draftName.trim();
    if (!naming || !name) return;
    if (naming.folder) {
      // A rename to the same string is a no-op; sending it would be a pointless
      // write and a pointless refetch of the whole tree.
      if (name !== naming.folder.name) onRename(naming.folder, name);
    } else {
      onCreate(name, naming.parentId, askForGroup ? draftGroup : null);
    }
    setNaming(null);
  };

  const fixed = (
    key: "all" | "unfiled",
    icon: JSX.Element,
    label: string,
    count: number
  ) => (
    <div
      className={`flex items-center gap-2 rounded-lg px-2 py-1.5 cursor-pointer transition-colors ${
        selection === key
          ? "bg-primary-500/15 text-text-primary"
          : "hover:bg-white/5 text-text-secondary"
      }`}
      onClick={() => onSelect(key)}
    >
      {icon}
      <span className="truncate flex-1 text-sm">{label}</span>
      <span className="text-xs text-text-muted tabular-nums">{count}</span>
    </div>
  );

  return (
    <div className="glass-card p-3 space-y-1">
      <div className="flex items-center justify-between px-2 pb-2">
        <h2 className="text-sm font-display font-semibold text-text-primary">
          {t("title")}
        </h2>
        <button
          type="button"
          className="p-1 text-text-muted hover:text-primary-400 transition-colors"
          title={t("newFolder")}
          onClick={() => openNaming({ folder: null, parentId: null })}
        >
          <FolderPlus className="w-4 h-4" />
        </button>
      </div>

      {fixed("all", <Layers className="w-4 h-4 shrink-0" />, t("all"), totalCount)}
      {fixed(
        "unfiled",
        <Inbox className="w-4 h-4 shrink-0" />,
        t("unfiled"),
        unfiledCount
      )}

      {roots.length > 0 && <div className="h-px bg-white/5 my-1" />}

      {roots.map((folder) => (
        <FolderRow
          key={folder.id}
          folder={folder}
          folders={folders}
          depth={0}
          selection={selection}
          onSelect={onSelect}
          onName={openNaming}
          onDelete={onDelete}
          t={t}
        />
      ))}

      {roots.length === 0 && (
        <p className="px-2 py-3 text-xs text-text-muted">{t("emptyHint")}</p>
      )}

      <Dialog
        isOpen={naming !== null}
        onClose={() => setNaming(null)}
        title={naming?.folder ? t("rename") : t("newFolder")}
        icon={<FolderPlus className="w-5 h-5 text-primary-400" />}
        maxWidth="sm"
      >
        <form onSubmit={submitName} className="space-y-4">
          <input
            type="text"
            className="input-field"
            value={draftName}
            onChange={(event) => setDraftName(event.target.value)}
            placeholder={t("newFolderPrompt")}
            aria-label={t("newFolderPrompt")}
            maxLength={255}
            // The dialog exists to be typed into; focusing anything else would
            // cost a click on every single use.
            autoFocus
          />
          {askForGroup && (
            <div>
              <label className="block text-xs text-text-secondary mb-1.5">
                {t("shareWith")}
              </label>
              <select
                className="input-field"
                value={draftGroup ?? ""}
                onChange={(event) =>
                  setDraftGroup(
                    event.target.value ? Number(event.target.value) : null
                  )
                }
              >
                <option value="">{t("shareWithNobody")}</option>
                {groups.map((group) => (
                  <option key={group.id} value={group.id}>
                    {group.name}
                  </option>
                ))}
              </select>
            </div>
          )}
          <div className="flex gap-3">
            <button
              type="button"
              className="btn-secondary flex-1"
              onClick={() => setNaming(null)}
            >
              {tCommon("cancel")}
            </button>
            <button
              type="submit"
              className="btn-primary flex-1"
              disabled={!draftName.trim()}
            >
              {tCommon("save")}
            </button>
          </div>
        </form>
      </Dialog>
    </div>
  );
}
