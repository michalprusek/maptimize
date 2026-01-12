"use client";

import { useState } from "react";
import { motion, AnimatePresence } from "framer-motion";
import { Search, ArrowUp, ArrowDown, Filter, X } from "lucide-react";

export type SortOrder = "asc" | "desc";

export interface SortOption<T extends string> {
  value: T;
  label: string;
}

export interface ProteinInfo {
  name: string;
  color?: string;
}

export interface ImageGalleryFiltersProps<TSortField extends string> {
  searchQuery: string;
  onSearchChange: (query: string) => void;
  searchPlaceholder?: string;
  sortField: TSortField;
  onSortFieldChange: (field: TSortField) => void;
  sortOrder: SortOrder;
  onSortOrderChange: (order: SortOrder) => void;
  sortOptions: SortOption<TSortField>[];
  proteinFilter?: string | null;
  onProteinFilterChange?: (protein: string | null) => void;
  availableProteins?: ProteinInfo[];
  onClearFilters: () => void;
  hasActiveFilters: boolean;
}

function getFilterButtonStyles(isActive: boolean): string {
  if (isActive) {
    return "bg-primary-500/20 text-primary-400";
  }
  return "bg-bg-secondary text-text-secondary hover:bg-bg-hover";
}

export function ImageGalleryFilters<TSortField extends string>({
  searchQuery,
  onSearchChange,
  searchPlaceholder = "Search...",
  sortField,
  onSortFieldChange,
  sortOrder,
  onSortOrderChange,
  sortOptions,
  proteinFilter,
  onProteinFilterChange,
  availableProteins = [],
  onClearFilters,
  hasActiveFilters,
}: ImageGalleryFiltersProps<TSortField>): JSX.Element {
  const [showFilters, setShowFilters] = useState(false);

  const hasProteinFilter =
    onProteinFilterChange !== undefined && availableProteins.length > 0;
  const SortIcon = sortOrder === "asc" ? ArrowUp : ArrowDown;

  return (
    <div className="glass-card p-4">
      <div className="flex items-center gap-4">
        {/* Search */}
        <div className="flex-1 relative">
          <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-text-muted" />
          <input
            type="text"
            value={searchQuery}
            onChange={(e) => onSearchChange(e.target.value)}
            placeholder={searchPlaceholder}
            className="input-field pl-10 w-full"
          />
        </div>

        {/* Sort */}
        <div className="flex items-center gap-2">
          <select
            value={sortField}
            onChange={(e) => onSortFieldChange(e.target.value as TSortField)}
            className="input-field text-sm"
          >
            {sortOptions.map((option) => (
              <option key={option.value} value={option.value}>
                {option.label}
              </option>
            ))}
          </select>
          <button
            onClick={() => onSortOrderChange(sortOrder === "asc" ? "desc" : "asc")}
            className="p-2 hover:bg-white/5 rounded-lg transition-colors"
            title={sortOrder === "asc" ? "Ascending" : "Descending"}
          >
            <SortIcon className="w-4 h-4 text-text-secondary" />
          </button>
        </div>

        {/* Filter toggle - only show if there are filters available */}
        {hasProteinFilter && (
          <button
            onClick={() => setShowFilters(!showFilters)}
            className={`p-2 rounded-lg transition-colors ${
              showFilters || hasActiveFilters
                ? "bg-primary-500/20 text-primary-400"
                : "hover:bg-white/5 text-text-secondary"
            }`}
          >
            <Filter className="w-4 h-4" />
          </button>
        )}

        {/* Clear filters */}
        {hasActiveFilters && (
          <button
            onClick={onClearFilters}
            className="text-sm text-text-muted hover:text-text-secondary flex items-center gap-1"
          >
            <X className="w-3 h-3" />
            Clear
          </button>
        )}
      </div>

      {/* Expanded filters */}
      <AnimatePresence>
        {showFilters && hasProteinFilter && (
          <motion.div
            initial={{ height: 0, opacity: 0 }}
            animate={{ height: "auto", opacity: 1 }}
            exit={{ height: 0, opacity: 0 }}
            className="overflow-hidden"
          >
            <div className="pt-4 mt-4 border-t border-white/5 flex flex-wrap gap-4">
              {/* Protein filter */}
              <div>
                <label className="block text-xs text-text-muted mb-2">
                  Protein
                </label>
                <div className="flex flex-wrap gap-1">
                  <button
                    onClick={() => onProteinFilterChange?.(null)}
                    className={`px-2 py-1 rounded text-xs transition-all ${getFilterButtonStyles(proteinFilter === null)}`}
                  >
                    All
                  </button>
                  {availableProteins.map((protein) => (
                    <button
                      key={protein.name}
                      onClick={() => onProteinFilterChange?.(protein.name)}
                      className={`px-2 py-1 rounded text-xs transition-all ${getFilterButtonStyles(proteinFilter === protein.name)}`}
                      style={{
                        borderColor:
                          proteinFilter === protein.name
                            ? protein.color
                            : undefined,
                      }}
                    >
                      {protein.name}
                    </button>
                  ))}
                </div>
              </div>
            </div>
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  );
}
