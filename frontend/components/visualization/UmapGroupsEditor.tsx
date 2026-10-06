"use client";

/**
 * Builds the reader's own comparison classes by dragging filter values into
 * groups. See `labelGroups.ts` for what a group is.
 *
 * A group is drawn as it is evaluated: one row of alternatives per facet
 * ("or"), rows joined by "and" — so "MTCL1 and Detyrosination" cannot be
 * mistaken for "MTCL1 or Detyrosination".
 *
 * Dragging is one of two ways in. Each group also has an "add values" toggle
 * that makes the next clicks on filter pills add to it — the same result from a
 * keyboard or a touch screen, where HTML drag events do not fire.
 */
import { useState } from "react";
import { Plus, Trash2, X } from "lucide-react";

import {
  MAX_GROUPS,
  MAX_GROUP_NAME_LENGTH,
  MEMBER_MIME,
  addGroup,
  addMember,
  decodeMember,
  groupColor,
  groupConditions,
  removeGroup,
  removeMember,
  renameGroup,
  type GroupCounts,
  type GroupMember,
  type LabelGroup,
} from "./labelGroups";
import type { Translate } from "./projectionShared";
import { FACET_LABEL_KEY } from "./umapFacets";

interface UmapGroupsEditorProps {
  groups: LabelGroup[];
  onChange: (groups: LabelGroup[]) => void;
  /** Display name and colour of a facet value, as the filter pills show it. */
  describe: (member: GroupMember) => { label: string; color: string };
  /** The group that pill clicks currently add to, if any. */
  armed: number | null;
  onArm: (index: number | null) => void;
  /** Points per group on the current plot; null while not coloured by groups. */
  counts: GroupCounts | null;
  /** The separability between these groups, drawn beside the title. */
  score: React.ReactNode;
  t: Translate;
}

/** Accept only our own pills — not files, links or text dragged from elsewhere. */
function carriesMember(event: React.DragEvent): boolean {
  return Array.from(event.dataTransfer.types).includes(MEMBER_MIME);
}

function droppedMember(event: React.DragEvent): GroupMember | null {
  return decodeMember(event.dataTransfer.getData(MEMBER_MIME));
}

export function UmapGroupsEditor({
  groups,
  onChange,
  describe,
  armed,
  onArm,
  counts,
  score,
  t,
}: UmapGroupsEditorProps): JSX.Element {
  // Which drop zone the pointer is over: a group index, or "new".
  const [over, setOver] = useState<number | "new" | null>(null);
  const full = groups.length >= MAX_GROUPS;
  const nextName = t("groupDefaultName", { n: groups.length + 1 });

  const zoneHandlers = (zone: number | "new", onMember: (member: GroupMember) => void) => ({
    onDragOver: (event: React.DragEvent) => {
      if (!carriesMember(event)) return;
      event.preventDefault();
      event.dataTransfer.dropEffect = "copy";
      setOver(zone);
    },
    onDragLeave: () => setOver((current) => (current === zone ? null : current)),
    onDrop: (event: React.DragEvent) => {
      event.preventDefault();
      setOver(null);
      const member = droppedMember(event);
      if (member) onMember(member);
    },
  });

  return (
    <div className="space-y-2" data-testid="umap-groups-editor">
      <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1">
        <span className="text-xs font-medium uppercase tracking-wide text-text-muted">
          {t("groupsTitle")}
        </span>
        {score && <span data-testid="umap-groups-score">{score}</span>}
        <span className="text-xs text-text-muted">{t("groupsHint")}</span>
      </div>

      <div className="grid grid-cols-1 md:grid-cols-2 gap-2">
        {groups.map((group, index) => {
          const color = groupColor(index);
          const isArmed = armed === index;
          return (
            <div
              key={index}
              data-testid={`umap-group-${index}`}
              {...zoneHandlers(index, (member) =>
                onChange(addMember(groups, index, member))
              )}
              className={`rounded-lg border p-2 space-y-2 transition-colors ${
                over === index || isArmed ? "bg-white/5" : ""
              }`}
              style={{ borderColor: over === index || isArmed ? color : `${color}50` }}
            >
              <div className="flex items-center gap-2">
                <span
                  className="w-3 h-3 rounded-full flex-shrink-0"
                  style={{ backgroundColor: color }}
                />
                <input
                  value={group.name}
                  maxLength={MAX_GROUP_NAME_LENGTH}
                  onChange={(event) =>
                    onChange(renameGroup(groups, index, event.target.value))
                  }
                  aria-label={t("groupNameLabel")}
                  className="input-field py-1 text-xs flex-1 min-w-0"
                />
                {counts && (
                  <span className="text-xs text-text-muted flex-shrink-0">
                    {t("groupPointCount", { count: counts.perGroup[index] ?? 0 })}
                  </span>
                )}
                <button
                  type="button"
                  aria-pressed={isArmed}
                  onClick={() => onArm(isArmed ? null : index)}
                  className={`px-2 py-1 rounded-md text-xs border flex-shrink-0 transition-colors ${
                    isArmed
                      ? "border-primary-500/60 bg-primary-500/20 text-text-primary"
                      : "border-white/10 text-text-secondary hover:text-text-primary hover:bg-white/5"
                  }`}
                >
                  {isArmed ? t("groupAddDone") : t("groupAddValues")}
                </button>
                <button
                  type="button"
                  onClick={() => {
                    // The armed index points at a slot; removing a group moves
                    // every later slot, so a stale arm would target a neighbour.
                    onArm(null);
                    onChange(removeGroup(groups, index));
                  }}
                  aria-label={t("groupRemove", { name: group.name })}
                  title={t("groupRemove", { name: group.name })}
                  className="p-1 rounded text-text-muted hover:text-accent-red hover:bg-white/5 flex-shrink-0"
                >
                  <Trash2 className="w-3.5 h-3.5" />
                </button>
              </div>

              <div className="flex flex-wrap gap-1.5 min-h-[26px]">
                {group.members.length === 0 && (
                  <span className="text-xs text-text-muted italic">
                    {isArmed ? t("groupArmedHint") : t("groupEmpty")}
                  </span>
                )}
                {groupConditions(group).map((condition, at) => (
                  <div
                    key={condition.facet}
                    className="flex flex-wrap items-center gap-1.5 w-full"
                  >
                    <span className="text-[10px] font-medium uppercase tracking-wide text-text-muted">
                      {at > 0 && (
                        <span className="text-text-secondary mr-1">{t("groupAnd")}</span>
                      )}
                      {t(FACET_LABEL_KEY[condition.facet])}
                    </span>
                    {condition.members.map((member, memberAt) => {
                      const shown = describe(member);
                      return (
                        <span
                          key={member.id}
                          className="inline-flex items-center gap-1.5"
                        >
                          {memberAt > 0 && (
                            <span className="text-[10px] text-text-muted">
                              {t("groupOr")}
                            </span>
                          )}
                          <button
                            type="button"
                            onClick={() => onChange(removeMember(groups, index, member))}
                            aria-label={t("groupRemoveValue", { name: shown.label })}
                            className="inline-flex items-center gap-1.5 px-2 py-1 rounded-md text-xs border border-white/10 text-text-primary hover:bg-white/5"
                          >
                            <span
                              className="w-2 h-2 rounded-full flex-shrink-0"
                              style={{ backgroundColor: shown.color }}
                            />
                            <span className="truncate max-w-[160px]">{shown.label}</span>
                            <X className="w-3 h-3 text-text-muted" />
                          </button>
                        </span>
                      );
                    })}
                  </div>
                ))}
              </div>
            </div>
          );
        })}

        {!full && (
          <button
            type="button"
            data-testid="umap-group-new"
            onClick={() => {
              const next = addGroup(groups, nextName);
              onChange(next);
              // A group made by clicking has nothing in it; arm it so the very
              // next clicks fill it, instead of leaving an empty box to puzzle over.
              onArm(next.length - 1);
            }}
            {...zoneHandlers("new", (member) =>
              onChange(addGroup(groups, nextName, member))
            )}
            className={`rounded-lg border border-dashed p-2 min-h-[72px] flex items-center justify-center gap-1.5 text-xs transition-colors ${
              over === "new"
                ? "border-primary-500 bg-primary-500/10 text-text-primary"
                : "border-white/15 text-text-muted hover:text-text-primary hover:bg-white/5"
            }`}
          >
            <Plus className="w-3.5 h-3.5" />
            {t("groupNew")}
          </button>
        )}
      </div>

      {counts && counts.ambiguous > 0 && (
        <p className="text-xs text-accent-amber" data-testid="umap-groups-overlap">
          {t("groupsOverlap", { count: counts.ambiguous })}
        </p>
      )}
    </div>
  );
}
