"use client";

import { useMutation, useQueryClient } from "@tanstack/react-query";
import { api } from "@/lib/api";

interface UseAssignPtmOptions {
  /** Shown when the request fails without a message of its own. */
  fallbackMessage: string;
  /** Reported to the user; the caller owns how errors are surfaced. */
  onError: (message: string) => void;
  /** Extra invalidations the calling screen needs (e.g. a single experiment). */
  onSuccess?: () => void;
}

/**
 * Assign (or clear) an experiment's PTM.
 *
 * SSOT for which caches this invalidates. Both the experiment list and the
 * experiment detail screen offer the assignment, and the non-obvious part is
 * `["umap"]`: the dashboard plot can be filtered by PTM, so its cached points go
 * stale the moment an assignment changes. Duplicating that knowledge per screen
 * is how one of them silently starts showing a stale plot.
 */
export function useAssignPtm({ fallbackMessage, onError, onSuccess }: UseAssignPtmOptions) {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: ({
      experimentId,
      ptmId,
    }: {
      experimentId: number;
      ptmId: number | null;
    }) => api.updateExperimentPtm(experimentId, ptmId),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["experiments"] });
      queryClient.invalidateQueries({ queryKey: ["umap"] });
      onSuccess?.();
    },
    onError: (err: Error) => {
      console.error("Failed to assign PTM:", err);
      onError(err.message || fallbackMessage);
    },
  });
}
