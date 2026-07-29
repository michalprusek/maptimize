"use client";

/**
 * Fetching whichever projection the dashboard is showing.
 *
 * The two endpoints answer with different names for the same three ideas — the
 * points, the facet summary, and "a fit is running / the fit failed" — so they
 * are normalised into one shape here. Everything downstream (the filter panel,
 * the legend, the tooltips, click-to-editor) then works on either without
 * knowing which is on screen, and only the things that genuinely differ, the
 * axis labels and the metric strip, are branched on.
 */
import { useQuery } from "@tanstack/react-query";

import {
  api,
  type DiscriminantMetrics,
  type UmapFacetRow,
  type UmapType,
} from "@/lib/api";
import { UMAP_STALE_POLL_MS } from "./chartConfig";
import { selectionKey, type FacetSelection } from "./umapFacets";
import type { ProjectionPoint } from "./projectionShared";

/** Which projection the dashboard is showing. */
export type Projection = "umap" | "lda";

/** The two projections reduced to what the shared UI needs. */
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
  /** UMAP only. */
  silhouetteScore: number | null;
  /** LDA only; null while the fit is still running. */
  metrics: DiscriminantMetrics | null;
}

export interface ProjectionDataResult {
  view: ProjectionView | undefined;
  isLoading: boolean;
  isFetching: boolean;
  error: unknown;
  refetch: () => void;
}

export function useProjectionData({
  projection,
  viewMode,
  selection,
  experimentId,
}: {
  projection: Projection;
  viewMode: UmapType;
  selection: FacetSelection;
  experimentId: number | undefined;
}): ProjectionDataResult {
  const key = selectionKey(selection);

  // Both queries stay mounted and are gated by `enabled`, so flipping the
  // segmented control back shows the cached projection immediately instead of
  // re-fetching a result that has not changed.
  const umap = useQuery({
    queryKey: ["umap", experimentId, viewMode, key],
    queryFn: () => api.getUmapData({ umapType: viewMode, selection }),
    enabled: projection === "umap",
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

  const discriminant = useQuery({
    queryKey: ["discriminant", experimentId, key],
    queryFn: () => api.getDiscriminantData({ selection }),
    enabled: projection === "lda",
    staleTime: 1000 * 60 * 5,
    retry: false,
    placeholderData: (previous) => previous,
    // The fit is minutes of work and never runs inside a request, so the first
    // read answers with is_computing and the coordinates follow.
    refetchInterval: (query) =>
      query.state.data?.is_computing ? UMAP_STALE_POLL_MS : false,
  });

  if (projection === "lda") {
    const data = discriminant.data;
    return {
      view: data && {
        points: data.points,
        facets: data.facets,
        totalCount: data.total_crops,
        // The labels are per-crop protein assignments, so there is no FOV-level
        // discriminant to offer.
        isFov: false,
        isComputing: data.is_computing,
        computeError: data.compute_error,
        silhouetteScore: null,
        metrics: data.metrics,
      },
      isLoading: discriminant.isLoading,
      isFetching: discriminant.isFetching,
      error: discriminant.error,
      refetch: discriminant.refetch,
    };
  }

  const data = umap.data;
  return {
    view: data && {
      points: data.points,
      facets: data.facets,
      totalCount: "total_images" in data ? data.total_images : data.total_crops,
      isFov: "total_images" in data,
      isComputing: data.is_stale,
      computeError: data.refresh_error,
      silhouetteScore: data.silhouette_score,
      metrics: null,
    },
    isLoading: umap.isLoading,
    isFetching: umap.isFetching,
    error: umap.error,
    refetch: umap.refetch,
  };
}
