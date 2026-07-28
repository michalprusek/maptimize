"use client";

import { useMemo, useState } from "react";
import { AnimatePresence, motion } from "framer-motion";
import { useTranslations } from "next-intl";
import { Filter, Search, X } from "lucide-react";

import { DEFAULT_POINT_COLOR } from "./chartConfig";
import {
  countActiveFilters,
  facetOptions,
  isSelectionEmpty,
  toggleFacetValue,
  type FacetKey,
  type FacetOption,
  type FacetSelection,
  type Named,
} from "./umapFacets";
import type { UmapFacetRow } from "@/lib/api";

/** Facets with more values than this get a search box. */
const SEARCHABLE_THRESHOLD = 12;

export type ColorBy = FacetKey;

interface UmapFilterPanelProps {
  rows: UmapFacetRow[];
  selection: FacetSelection;
  onSelectionChange: (selection: FacetSelection) => void;
  colorBy: ColorBy;
  onColorByChange: (colorBy: ColorBy) => void;
  microscopes: Named[] | undefined;
  proteins: Named[] | undefined;
  ptms: Named[] | undefined;
  /** Hidden when the plot is already scoped to one experiment. */
  showExperimentFacet: boolean;
  shownCount: number;
  totalCount: number;
}

function FacetPill({
  option,
  selected,
  onToggle,
}: {
  option: FacetOption;
  selected: boolean;
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
    </button>
  );
}

function FacetSection({
  label,
  options,
  selected,
  onToggle,
  onClear,
  clearLabel,
  searchPlaceholder,
}: {
  label: string;
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
        {visible.map((option) => (
          <FacetPill
            key={option.id}
            option={option}
            selected={selected.includes(option.id)}
            onToggle={() => onToggle(option.id)}
          />
        ))}
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
  microscopes,
  proteins,
  ptms,
  showExperimentFacet,
  shownCount,
  totalCount,
}: UmapFilterPanelProps): JSX.Element {
  const t = useTranslations("umap");
  const [expanded, setExpanded] = useState(false);

  const unassigned = t("unassigned");
  const options = useMemo(
    () => ({
      experiment: facetOptions(rows, "experiment", undefined, unassigned),
      microscope: facetOptions(rows, "microscope", microscopes, unassigned),
      protein: facetOptions(rows, "protein", proteins, unassigned),
      ptm: facetOptions(rows, "ptm", ptms, unassigned),
    }),
    [rows, microscopes, proteins, ptms, unassigned]
  );

  const activeCount = countActiveFilters(selection);
  const facets: Array<{ key: FacetKey; label: string; hidden?: boolean }> = [
    { key: "experiment", label: t("facetExperiment"), hidden: !showExperimentFacet },
    { key: "microscope", label: t("facetMicroscope") },
    { key: "protein", label: t("facetProtein") },
    { key: "ptm", label: t("facetPtm") },
  ];

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
            onClick={() =>
              onSelectionChange({ experiment: [], microscope: [], protein: [], ptm: [] })
            }
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
              <option value="protein">{t("facetProtein")}</option>
              <option value="microscope">{t("facetMicroscope")}</option>
              <option value="ptm">{t("facetPtm")}</option>
              <option value="experiment">{t("facetExperiment")}</option>
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
            <div className="grid grid-cols-1 md:grid-cols-2 gap-4 pt-3">
              {facets
                .filter((facet) => !facet.hidden)
                .map((facet) => (
                  <FacetSection
                    key={facet.key}
                    label={facet.label}
                    options={options[facet.key]}
                    selected={selection[facet.key]}
                    onToggle={(id) =>
                      onSelectionChange(toggleFacetValue(selection, facet.key, id))
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
