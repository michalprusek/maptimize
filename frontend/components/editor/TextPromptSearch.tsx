"use client";

/**
 * TextPromptSearch Component
 *
 * Search bar for SAM 3 text-based segmentation.
 * Allows users to type natural language descriptions to find objects.
 */

import { useState, useCallback, useRef, useEffect } from "react";
import { useTranslations } from "next-intl";
import { Search, ArrowRight, Loader2, X } from "lucide-react";
import type { DetectedInstance } from "@/lib/editor/types";

interface TextPromptSearchProps {
  /** Current text prompt value */
  value: string;
  /** Called when value changes */
  onChange: (value: string) => void;
  /** Called when user submits the query */
  onSubmit: () => void;
  /** Whether query is loading */
  isLoading: boolean;
  /** Placeholder text */
  placeholder?: string;
  /** Quick suggestion prompts */
  suggestions?: string[];
  /** Detected instances (to show count) */
  detectedInstances?: DetectedInstance[];
  /** Selected instance index */
  selectedInstanceIndex?: number | null;
  /** Called when instance is selected */
  onSelectInstance?: (index: number) => void;
  /** Called when instance is saved */
  onSaveInstance?: (index: number) => void;
  /** Called to clear results */
  onClear?: () => void;
  /** Error message */
  error?: string | null;
}

export function TextPromptSearch({
  value,
  onChange,
  onSubmit,
  isLoading,
  placeholder,
  suggestions = ["cell", "nucleus", "membrane", "organelle"],
  detectedInstances = [],
  selectedInstanceIndex,
  onSelectInstance,
  onSaveInstance,
  onClear,
  error,
}: TextPromptSearchProps) {
  const t = useTranslations("editor");
  const inputRef = useRef<HTMLInputElement>(null);
  const [showSuggestions, setShowSuggestions] = useState(false);

  // Focus input on mount
  useEffect(() => {
    inputRef.current?.focus();
  }, []);

  const handleKeyDown = useCallback(
    (e: React.KeyboardEvent) => {
      if (e.key === "Enter" && value.trim()) {
        e.preventDefault();
        onSubmit();
        setShowSuggestions(false);
      }
      if (e.key === "Escape") {
        setShowSuggestions(false);
        onClear?.();
      }
    },
    [value, onSubmit, onClear]
  );

  const handleSuggestionClick = useCallback(
    (suggestion: string) => {
      onChange(suggestion);
      setShowSuggestions(false);
      // Auto-submit after selecting suggestion
      setTimeout(() => onSubmit(), 50);
    },
    [onChange, onSubmit]
  );

  const handleClear = useCallback(() => {
    onChange("");
    onClear?.();
    inputRef.current?.focus();
  }, [onChange, onClear]);

  return (
    <div className="space-y-2">
      {/* Search input */}
      <div className="relative">
        <div className="flex items-center gap-2 bg-bg-secondary/90 backdrop-blur-sm rounded-xl border border-white/10 px-3 py-2.5 focus-within:border-primary-500/50 transition-colors">
          <Search className="w-4 h-4 text-text-secondary flex-shrink-0" />
          <input
            ref={inputRef}
            type="text"
            value={value}
            onChange={(e) => {
              onChange(e.target.value);
              setShowSuggestions(true);
            }}
            onKeyDown={handleKeyDown}
            onFocus={() => setShowSuggestions(true)}
            placeholder={placeholder || t("searchPlaceholder")}
            className="flex-1 bg-transparent text-sm text-text-primary placeholder:text-text-muted outline-none min-w-0"
            disabled={isLoading}
          />
          {isLoading ? (
            <Loader2 className="w-4 h-4 animate-spin text-primary-500 flex-shrink-0" />
          ) : value ? (
            <button
              onClick={handleClear}
              className="p-1 hover:bg-white/10 rounded transition-colors"
              title={t("clear")}
            >
              <X className="w-3.5 h-3.5 text-text-secondary" />
            </button>
          ) : (
            <button
              onClick={onSubmit}
              disabled={!value.trim()}
              className="p-1 hover:bg-white/10 rounded transition-colors disabled:opacity-50"
              title={t("search")}
            >
              <ArrowRight className="w-4 h-4 text-text-secondary" />
            </button>
          )}
        </div>

        {/* Suggestions dropdown */}
        {showSuggestions && !value && suggestions.length > 0 && !isLoading && (
          <div className="absolute top-full mt-1 left-0 right-0 bg-bg-secondary/95 backdrop-blur-sm rounded-lg border border-white/10 p-2 z-10">
            <div className="text-xs text-text-muted mb-1.5">{t("suggestions")}</div>
            <div className="flex flex-wrap gap-1.5">
              {suggestions.map((s) => (
                <button
                  key={s}
                  onClick={() => handleSuggestionClick(s)}
                  className="px-2.5 py-1 text-xs bg-white/5 hover:bg-white/10 rounded-lg text-text-secondary hover:text-text-primary transition-colors"
                >
                  {s}
                </button>
              ))}
            </div>
          </div>
        )}
      </div>

      {/* Error message */}
      {error && (
        <div className="px-3 py-2 bg-red-500/10 border border-red-500/20 rounded-lg text-xs text-red-400">
          {error}
        </div>
      )}

      {/* Results count */}
      {detectedInstances.length > 0 && !isLoading && (
        <div className="text-xs text-text-secondary px-1">
          {t("foundInstances", { count: detectedInstances.length })}
        </div>
      )}

      {/* Instance list */}
      {detectedInstances.length > 0 && (
        <div className="bg-bg-secondary/80 backdrop-blur-sm rounded-xl border border-white/10 overflow-hidden">
          <div className="max-h-48 overflow-y-auto">
            {detectedInstances.map((instance) => (
              <InstanceItem
                key={instance.index}
                instance={instance}
                isSelected={selectedInstanceIndex === instance.index}
                onSelect={() => onSelectInstance?.(instance.index)}
                onSave={() => onSaveInstance?.(instance.index)}
              />
            ))}
          </div>
        </div>
      )}

      {/* No results message */}
      {value && detectedInstances.length === 0 && !isLoading && !error && (
        <div className="text-xs text-text-muted px-1">
          {t("noInstancesFound")}
        </div>
      )}
    </div>
  );
}

/**
 * Single instance item in the list.
 */
interface InstanceItemProps {
  instance: DetectedInstance;
  isSelected: boolean;
  onSelect: () => void;
  onSave: () => void;
}

function InstanceItem({ instance, isSelected, onSelect, onSave }: InstanceItemProps) {
  const t = useTranslations("editor");

  // Color based on index for visual distinction
  const colors = [
    "bg-emerald-500",
    "bg-blue-500",
    "bg-purple-500",
    "bg-orange-500",
    "bg-pink-500",
    "bg-cyan-500",
  ];
  const colorClass = colors[instance.index % colors.length];

  return (
    <div
      onClick={onSelect}
      className={`flex items-center justify-between px-3 py-2 cursor-pointer transition-colors border-b border-white/5 last:border-b-0 ${
        isSelected
          ? "bg-primary-500/20"
          : "hover:bg-white/5"
      }`}
    >
      <div className="flex items-center gap-2.5">
        {/* Color indicator */}
        <div className={`w-3 h-3 rounded-full ${colorClass}`} />
        {/* Instance number */}
        <span className="text-sm text-text-primary font-medium">
          #{instance.index + 1}
        </span>
        {/* Confidence */}
        <span className="text-xs text-text-secondary">
          {Math.round(instance.score * 100)}%
        </span>
      </div>

      {/* Save button */}
      <button
        onClick={(e) => {
          e.stopPropagation();
          onSave();
        }}
        className="px-2 py-1 text-xs bg-primary-500/20 hover:bg-primary-500/30 text-primary-400 rounded transition-colors"
      >
        {t("saveInstance")}
      </button>
    </div>
  );
}
