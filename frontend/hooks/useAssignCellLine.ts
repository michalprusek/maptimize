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
 * `["cellLines"]` is deliberately NOT invalidated. The list carries a per-line
 * `experiment_count`, which an assignment does change — but nothing renders it
 * (there is no cell-line admin page, and the picker's second line is the
 * description), so refetching it would be a request per assignment bought with
 * a justification that is not true.
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
      queryClient.invalidateQueries({ queryKey: ["umap"] });
      onSuccess?.();
    },
    onError: (err: Error) => {
      console.error("Failed to assign cell line:", err);
      onError(err.message || fallbackMessage);
    },
  });
}
