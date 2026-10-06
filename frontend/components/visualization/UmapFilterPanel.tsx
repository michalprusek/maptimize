"use client";

import { useMemo, useState } from "react";
import { AnimatePresence, motion } from "framer-motion";
import { useTranslations } from "next-intl";
import { Filter, Search, X } from "lucide-react";

import { DEFAULT_POINT_COLOR } from "./chartConfig";
import {
  COLOR_BY_ORDER,
  EMPTY_SELECTION,
  FACET_ORDER,
  countActiveFilters,
  facetOptions,
  isSelectionEmpty,
  toggleFacetValue,
  FACET_LABEL_KEY,
  LABEL_AXIS_KEY,
  type FacetKey,
  type FacetOption,
  type FacetSelection,
  type Named,
} from "./umapFacets";
import {
  MEMBER_MIME,
  addMember,
  encodeMember,
  groupColor,
  groupsOfMember,
  type GroupCounts,
  type GroupMember,
  type LabelGroup,
} from "./labelGroups";
import { UmapGroupsEditor } from "./UmapGroupsEditor";
import type { LabelAxis, UmapFacetRow } from "@/lib/api";

/** Facets with more values than this get a search box. */
const SEARCHABLE_THRESHOLD = 12;

/** A facet, or the reader's own groups. */
export type ColorBy = LabelAxis;

interface UmapFilterPanelProps {
  rows: UmapFacetRow[];
  selection: FacetSelection;
  onSelectionChange: (selection: FacetSelection) => void;
  colorBy: ColorBy;
  onColorByChange: (colorBy: ColorBy) => void;
  /** The reader's comparison classes; values are dragged into them from here. */
  groups: LabelGroup[];
  onGroupsChange: (groups: LabelGroup[]) => void;
  /** Points per group on the current plot; null unless coloured by groups. */
  groupCounts: GroupCounts | null;
  microscopes: Named[] | undefined;
  proteins: Named[] | undefined;
  ptms: Named[] | undefined;
  cellLines: Named[] | undefined;
  /** Hidden when the plot is already scoped to one experiment. */
  showExperimentFacet: boolean;
  shownCount: number;
  totalCount: number;
}

function FacetPill({
  option,
  member,
  selected,
  heldBy,
  onToggle,
}: {
  option: FacetOption;
  /** This value as a group member — what a drag carries. */
  member: GroupMember;
  selected: boolean;
  /** Every group this value is a condition of. */
  heldBy: Array<{ index: number; name: string }>;
  onToggle: () => void;
}): JSX.Element {
  const color = option.color || DEFAULT_POINT_COLOR;
  // Empty values stay clickable but read as inactive, so "no data yet" is
  // visibly different from "does not exist".
  const empty = option.count === 0;

  return (
    <button
      type="button"
      onClick={onToggle}
      aria-pressed={selected}
      draggable
      onDragStart={(event) => {
        event.dataTransfer.setData(MEMBER_MIME, encodeMember(member));
        event.dataTransfer.effectAllowed = "copy";
      }}
      className={`flex items-center gap-1.5 px-2 py-1 rounded-md text-xs border transition-colors ${
        selected
          ? "text-text-primary"
          : "border-white/10 text-text-secondary hover:text-text-primary hover:bg-white/5"
      } ${empty && !selected ? "opacity-40" : ""}`}
      style={
        selected
          ? { backgroundColor: `${color}20`, borderColor: `${color}60` }
          : undefined
      }
    >
      <span
        className="w-2.5 h-2.5 rounded-full flex-shrink-0"
        style={{ backgroundColor: color }}
      />
      <span className="truncate max-w-[160px]">{option.name}</span>
      <span className="text-text-muted">{option.count}</span>
      {heldBy.map((group) => (
        // One square per group that names this value, in that group's colour —
        // and its name, since colour alone says nothing to some readers.
        <span
          key={group.index}
          role="img"
          aria-label={group.name}
          title={group.name}
          className="w-2 h-2 rounded-sm flex-shrink-0"
          style={{ backgroundColor: groupColor(group.index) }}
        />
      ))}
    </button>
  );
}

function FacetSection({
  label,
  facet,
  groups,
  options,
  selected,
  onToggle,
  onClear,
  clearLabel,
  searchPlaceholder,
}: {
  label: string;
  facet: FacetKey;
  groups: LabelGroup[];
  options: FacetOption[];
  selected: number[];
  onToggle: (id: number) => void;
  onClear: () => void;
  clearLabel: string;
  searchPlaceholder: string;
}): JSX.Element | null {
  const [search, setSearch] = useState("");

  const visible = useMemo(() => {
    const needle = search.trim().toLowerCase();
    if (!needle) return options;
    return options.filter((option) => option.name.toLowerCase().includes(needle));
  }, [options, search]);

  if (options.length === 0) return null;

  return (
    <div className="space-y-2">
      <div className="flex items-center justify-between gap-2">
        <span className="text-xs font-medium uppercase tracking-wide text-text-muted">
          {label}
        </span>
        {selected.length > 0 && (
          <button
            type="button"
            onClick={onClear}
            className="text-xs text-text-muted hover:text-text-primary"
          >
            {clearLabel}
          </button>
        )}
      </div>

      {options.length > SEARCHABLE_THRESHOLD && (
        <div className="relative">
          <Search className="w-3.5 h-3.5 absolute left-2.5 top-1/2 -translate-y-1/2 text-text-muted" />
          <input
            value={search}
            onChange={(event) => setSearch(event.target.value)}
            placeholder={searchPlaceholder}
            className="input-field py-1.5 pl-8 text-xs"
          />
        </div>
      )}

      <div className="flex flex-wrap gap-1.5">
        {visible.map((option) => {
          const member = { facet, id: option.id };
          return (
            <FacetPill
              key={option.id}
              option={option}
              member={member}
              heldBy={groupsOfMember(groups, member).map((index) => ({
                index,
                name: groups[index].name,
              }))}
              selected={selected.includes(option.id)}
              onToggle={() => onToggle(option.id)}
            />
          );
        })}
      </div>
    </div>
  );
}

export function UmapFilterPanel({
  rows,
  selection,
  onSelectionChange,
  colorBy,
  onColorByChange,
  groups,
  onGroupsChange,
  groupCounts,
  microscopes,
  proteins,
  ptms,
  cellLines,
  showExperimentFacet,
  shownCount,
  totalCount,
}: UmapFilterPanelProps): JSX.Element {
  const t = useTranslations("umap");
  const [expanded, setExpanded] = useState(false);
  // The group that pill clicks add to. While one is armed a click on a pill
  // fills that group INSTEAD of toggling the filter — the non-drag way in.
  const [armedGroup, setArmedGroup] = useState<number | null>(null);
  const armed = armedGroup !== null && armedGroup < groups.length ? armedGroup : null;

  const unassigned = t("unassigned");
  const options = useMemo(
    () => ({
      experiment: facetOptions(rows, "experiment", undefined, unassigned),
      microscope: facetOptions(rows, "microscope", microscopes, unassigned),
      protein: facetOptions(rows, "protein", proteins, unassigned),
      ptm: facetOptions(rows, "ptm", ptms, unassigned),
      cell_line: facetOptions(rows, "cell_line", cellLines, unassigned),
    }),
    [rows, microscopes, proteins, ptms, cellLines, unassigned]
  );

  const activeCount = countActiveFilters(selection);
  // ⚠️ Derived, never a second list. The previous hand-written array is how the
  // cell-line facet shipped with working options that rendered no sections.
  const facets = FACET_ORDER.map((key) => ({
    key,
    label: t(FACET_LABEL_KEY[key]),
    // The plot is already scoped to one experiment; offering the facet would
    // only let the reader contradict that scope.
    hidden: key === "experiment" && !showExperimentFacet,
  }));

  // Chips summarising what is active, so the filter is readable while collapsed.
  const activeChips = facets
    .filter((facet) => !facet.hidden)
    .flatMap((facet) =>
      selection[facet.key].map((id) => {
        const option = options[facet.key].find((candidate) => candidate.id === id);
        return {
          facet: facet.key,
          id,
          label: option?.name ?? `#${id}`,
          color: option?.color || DEFAULT_POINT_COLOR,
        };
      })
    );

  return (
    <div className="mb-4 border-b border-white/5 pb-3">
      <div className="flex flex-wrap items-center gap-2">
        <button
          type="button"
          onClick={() => setExpanded((open) => !open)}
          className={`inline-flex items-center gap-1.5 px-2.5 py-1.5 rounded-lg text-sm border transition-colors ${
            expanded || activeCount > 0
              ? "border-primary-500/50 bg-primary-500/10 text-text-primary"
              : "border-white/10 text-text-secondary hover:text-text-primary hover:bg-white/5"
          }`}
        >
          <Filter className="w-4 h-4" />
          {t("filters")}
          {activeCount > 0 && (
            <span className="px-1.5 rounded-full bg-primary-500 text-white text-xs">
              {activeCount}
            </span>
          )}
        </button>

        {activeChips.map((chip) => (
          <button
            key={`${chip.facet}-${chip.id}`}
            type="button"
            onClick={() => onSelectionChange(toggleFacetValue(selection, chip.facet, chip.id))}
            className="inline-flex items-center gap-1.5 px-2 py-1 rounded-md text-xs border text-text-primary"
            style={{ backgroundColor: `${chip.color}20`, borderColor: `${chip.color}60` }}
          >
            <span
              className="w-2 h-2 rounded-full flex-shrink-0"
              style={{ backgroundColor: chip.color }}
            />
            {chip.label}
            <X className="w-3 h-3 text-text-muted" />
          </button>
        ))}

        {!isSelectionEmpty(selection) && (
          <button
            type="button"
            onClick={() => onSelectionChange(EMPTY_SELECTION)}
            className="text-xs text-text-muted hover:text-text-primary underline"
          >
            {t("clearAll")}
          </button>
        )}

        <div className="ml-auto flex items-center gap-2">
          <span className="text-xs text-text-muted">
            {t("showingPoints", { shown: shownCount, total: totalCount })}
          </span>
          <label className="flex items-center gap-1.5 text-xs text-text-muted">
            {t("colorBy")}
            <select
              value={colorBy}
              onChange={(event) => onColorByChange(event.target.value as ColorBy)}
              className="input-field py-1 text-xs w-auto"
            >
              {COLOR_BY_ORDER.map((key) => (
                <option key={key} value={key}>
                  {t(LABEL_AXIS_KEY[key])}
                </option>
              ))}
            </select>
          </label>
        </div>
      </div>

      <AnimatePresence initial={false}>
        {expanded && (
          <motion.div
            initial={{ height: 0, opacity: 0 }}
            animate={{ height: "auto", opacity: 1 }}
            exit={{ height: 0, opacity: 0 }}
            transition={{ duration: 0.18 }}
            className="overflow-hidden"
          >
            <div className="pt-3">
              <UmapGroupsEditor
                groups={groups}
                onChange={onGroupsChange}
                describe={(member) => {
                  const option = options[member.facet].find(
                    (candidate) => candidate.id === member.id
                  );
                  return {
                    label: option?.name ?? `#${member.id}`,
                    color: option?.color || DEFAULT_POINT_COLOR,
                  };
                }}
                armed={armed}
                onArm={setArmedGroup}
                counts={groupCounts}
                t={t}
              />
            </div>
            <div className="grid grid-cols-1 md:grid-cols-2 gap-4 pt-3">
              {facets
                .filter((facet) => !facet.hidden)
                .map((facet) => (
                  <FacetSection
                    key={facet.key}
                    label={facet.label}
                    facet={facet.key}
                    groups={groups}
                    options={options[facet.key]}
                    selected={selection[facet.key]}
                    onToggle={(id) =>
                      armed === null
                        ? onSelectionChange(toggleFacetValue(selection, facet.key, id))
                        : onGroupsChange(
                            addMember(groups, armed, { facet: facet.key, id })
                          )
                    }
                    onClear={() => onSelectionChange({ ...selection, [facet.key]: [] })}
                    clearLabel={t("clear")}
                    searchPlaceholder={t("searchFacet")}
                  />
                ))}
            </div>
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  );
}
