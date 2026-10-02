"use client";

import { useMutation, useQueryClient } from "@tanstack/react-query";
import { api } from "@/lib/api";

interface UseAssignCellLineOptions {
  /** Shown when the request fails without a message of its own. */
  fallbackMessage: string;
  /** Reported to the user; the caller owns how errors are surfaced. */
  onError: (message: string) => void;
  /** Extra invalidations the calling screen needs (e.g. a single experiment). */
  onSuccess?: () => void;
}

/**
 * Assign (or clear) an experiment's cell line.
 *
 * SSOT for which caches this invalidates, mirroring `useAssignPtm`. `["umap"]`
 * is the non-obvious one: the dashboard plot can be filtered and coloured by
 * cell line, so its cached points go stale the moment an assignment changes.
 * Duplicating that knowledge per screen is how one of them silently starts
 * showing a stale plot.
 *
 * ⚠️ `["cellLines"]` IS invalidated, and the reason is newer than this hook.
 * It was dropped in review on the grounds that nothing rendered the per-line
 * `experiment_count` — true at the time. The picker's trash now reads exactly
 * that count to decide whether deleting is allowed, so a stale one either arms
 * the button for a line in use (a 409 after the user confirms) or disables it
 * for one that is free. The justification reversed; so did the code.
 */
export function useAssignCellLine({
  fallbackMessage,
  onError,
  onSuccess,
}: UseAssignCellLineOptions) {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: ({
      experimentId,
      cellLineId,
    }: {
      experimentId: number;
      cellLineId: number | null;
    }) => api.updateExperimentCellLine(experimentId, cellLineId),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["experiments"] });
      queryClient.invalidateQueries({ queryKey: ["cellLines"] });
      queryClient.invalidateQueries({ queryKey: ["umap"] });
      onSuccess?.();
    },
    onError: (err: Error) => {
      console.error("Failed to assign cell line:", err);
      onError(err.message || fallbackMessage);
    },
  });
}
