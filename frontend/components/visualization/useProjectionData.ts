"use client";

/**
 * Fetching the dashboard's UMAP.
 *
 * One endpoint, two corpora. The cropped and FOV responses are near-identical —
 * `points`, `facets`, `is_stale`, `refresh_error` and `separability` are
 * named the same — and differ in exactly one field: `total_crops` vs
 * `total_images`. That single difference is collapsed here into `totalCount`
 * plus an `isFov` flag, so nothing downstream repeats the `in`-check to find out
 * which corpus it is looking at.
 */
import { useQuery } from "@tanstack/react-query";

import {
  api,
  type LabelAxis,
  type Separability,
  type SeparabilityUnscored,
  type UmapFacetRow,
  type UmapType,
} from "@/lib/api";
import { UMAP_STALE_POLL_MS } from "./chartConfig";
import { selectionKey, type FacetSelection } from "./umapFacets";
import type { ProjectionPoint } from "./projectionShared";

/** The projection reduced to what the shared UI needs. */
export interface ProjectionView {
  points: ProjectionPoint[];
  /** Buckets covering the scope BEFORE facet filters — the filter panel's input. */
  facets: UmapFacetRow[];
  totalCount: number;
  isFov: boolean;
  /** A fit is running in the background; the caller polls until this clears. */
  isComputing: boolean;
  /** The fit failed, so nothing is coming on its own. */
  computeError: string | null;
  /** Null when too few of these points carry a value on the requested axis. */
  separability: Separability | null;
  /** Why there is no score. Set whenever points came back without one. */
  separabilityUnscored: SeparabilityUnscored | null;
  /**
   * These are the previous filter's results, kept on screen while the new ones
   * load. Anything that states a number about "the current filter" must say so.
   */
  isPrevious: boolean;
  /** The slot arrangement the points' `group` indices were assigned against. */
  groupEpoch: number;
}

export interface ProjectionDataResult {
  view: ProjectionView | undefined;
  isLoading: boolean;
  isFetching: boolean;
  error: unknown;
  refetch: () => void;
}

export function useProjectionData({
  viewMode,
  selection,
  experimentId,
  labelBy,
  groups,
  groupEpoch,
}: {
  viewMode: UmapType;
  selection: FacetSelection;
  experimentId: number | undefined;
  /** Axis the separability score groups by — the caller passes its colour-by. */
  labelBy: LabelAxis;
  /** The reader's groups in wire form; sent only when `labelBy` is "group". */
  groups: string[];
  /** Bumped whenever an edit moves a group to another slot (see labelGroups). */
  groupEpoch: number;
}): ProjectionDataResult {
  const key = selectionKey(selection);
  // Under any other axis the groups do not reach the request, so they must not
  // reach the key either — editing them would refetch an identical response.
  //
  // The epoch is part of it: a cached response is tagged with the epoch it was
  // fetched in, so reusing one across epochs would serve indices that can never
  // be read and are never refetched while they count as fresh.
  const groupsKey = labelBy === "group" ? `${groupEpoch}:${groups.join("|")}` : "";

  const umap = useQuery({
    // labelBy is part of the key because it changes the response, not just the
    // rendering: leaving it out serves a cached score for the previous axis.
    queryKey: ["umap", experimentId, viewMode, key, labelBy, groupsKey],
    queryFn: async () => ({
      ...(await api.getUmapData({ umapType: viewMode, selection, labelBy, groups })),
      // Which arrangement of group slots this response's `point.group` indexes
      // into. The previous response stays on screen while the next one loads,
      // and after a group is deleted its indices point one slot off.
      groupEpoch,
    }),
    staleTime: 1000 * 60 * 5, // Cache for 5 minutes
    retry: false,
    // Keep the previous result on screen while a new filter loads. Without it
    // every pill click makes the data undefined for a moment, and the panel is
    // rendered conditionally on it — so it unmounts mid-interaction and loses
    // its expanded state and any text typed into a facet search.
    placeholderData: (previous) => previous,
    // New uploads/edits arrive without coordinates; the request that observes
    // that schedules a background re-fit. Poll until those coordinates land.
    refetchInterval: (query) =>
      query.state.data?.is_stale ? UMAP_STALE_POLL_MS : false,
  });

  const data = umap.data;
  return {
    view: data && {
      points: data.points,
      facets: data.facets,
      totalCount: "total_images" in data ? data.total_images : data.total_crops,
      isFov: "total_images" in data,
      isComputing: data.is_stale,
      computeError: data.refresh_error,
      separability: data.separability,
      // `?? null`: a backend older than this field omits it, and `undefined`
      // would slip past a `!== null` check into a render that reads `.reason`.
      separabilityUnscored: data.separability_unscored ?? null,
      isPrevious: umap.isPlaceholderData,
      groupEpoch: data.groupEpoch,
    },
    isLoading: umap.isLoading,
    isFetching: umap.isFetching,
    error: umap.error,
    refetch: umap.refetch,
  };
}
