"use client";

import { useCallback } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { api } from "@/lib/api";

interface UseCreateCellLineOptions {
  /** Shown when the request fails without a message of its own. */
  fallbackMessage: string;
  /** Reported to the user; the caller owns how errors are surfaced. */
  onError: (message: string) => void;
  /** Called after a success, so a screen can clear its error banner. */
  onSuccess?: () => void;
}

/**
 * Mint a cell line from a name typed into a `ColorTagSelect`.
 *
 * Shaped to be passed straight to that component's `onCreate`: it takes the
 * trimmed name and resolves to the new id, or to null when the request failed.
 * Null is what keeps the menu open with the typing intact, so the error lands
 * next to the box it came from.
 *
 * A hook rather than a function on each screen because the experiment list and
 * the experiment detail page both offer the picker, and the part worth sharing
 * is not the POST — it is the `["cellLines"]` invalidation after it. A copy that
 * forgot it would create the line, select it, and leave every other picker on
 * the page still listing the old set.
 *
 * Deliberately not `useMutation`: the picker needs the id as a return value, and
 * a mutation's `mutateAsync` would still need this wrapper to turn a rejection
 * into null.
 */
export function useCreateCellLine({
  fallbackMessage,
  onError,
  onSuccess,
}: UseCreateCellLineOptions) {
  const queryClient = useQueryClient();

  return useCallback(
    async (name: string): Promise<number | null> => {
      try {
        const created = await api.createCellLine({ name });
        await queryClient.invalidateQueries({ queryKey: ["cellLines"] });
        onSuccess?.();
        return created.id;
      } catch (err) {
        const message = err instanceof Error ? err.message : "";
        onError(message || fallbackMessage);
        return null;
      }
    },
    [queryClient, fallbackMessage, onError, onSuccess]
  );
}
