"use client";

import { useMutation, useQueryClient } from "@tanstack/react-query";
import { api } from "@/lib/api";

interface UseDeleteCellLineOptions {
  /** Shown when the request fails without a message of its own. */
  fallbackMessage: string;
  /** Reported to the user; the caller owns how errors are surfaced. */
  onError: (message: string) => void;
  /** Called after a success, so a screen can clear its banner and close its modal. */
  onSuccess?: () => void;
}

/**
 * Delete a cell line.
 *
 * SSOT for which caches this invalidates, mirroring `useAssignCellLine`. The
 * line is shared reference data with no admin page, so this is the only delete
 * path in the product and every picker on the page must stop offering the row.
 *
 * `["experiments"]` is invalidated even though the backend refuses (409) to
 * delete a line any experiment holds: the refusal is what makes that safe, not
 * an assumption, and a stale list is the cheaper thing to be wrong about.
 *
 * ⚠️ `["umap"]` is NOT invalidated. A deletable line is by definition on zero
 * experiments, so no plotted point can carry it and no facet pill can show it —
 * refetching thousands of coordinates would buy nothing.
 */
export function useDeleteCellLine({
  fallbackMessage,
  onError,
  onSuccess,
}: UseDeleteCellLineOptions) {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: (cellLineId: number) => api.deleteCellLine(cellLineId),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["cellLines"] });
      queryClient.invalidateQueries({ queryKey: ["experiments"] });
      onSuccess?.();
    },
    onError: (err: Error) => {
      console.error("Failed to delete cell line:", err);
      onError(err.message || fallbackMessage);
    },
  });
}
