"use client";

import { useState, useMemo, useCallback, useEffect } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useTranslations } from "next-intl";
import { useRouter } from "next/navigation";
import {
  ScatterChart,
  Scatter,
  XAxis,
  YAxis,
  ZAxis,
  ResponsiveContainer,
  Cell,
  Tooltip,
} from "recharts";
import { api, UmapType } from "@/lib/api";
import { Spinner } from "@/components/ui";
import { RefreshCw, Info, AlertCircle, Grid, Layers, FilterX } from "lucide-react";
import {
  DEFAULT_POINT_COLOR,
  UMAP_AXIS_STYLE,
  UMAP_AXIS_DOMAIN,
  UMAP_TOOLTIP_CURSOR,
  UMAP_SCATTER_ANIMATION,
  formatAxisTick,
  getSilhouetteScoreStyle,
} from "./chartConfig";
import { UmapFilterPanel, type ColorBy } from "./UmapFilterPanel";
import {
  CroppedTooltip,
  FovTooltip,
  MarkerLegend,
  ProjectionLegend,
  ProjectionMarker,
  type PointContext,
  type ProjectionPoint,
  type RechartsShapeProps,
} from "./projectionShared";
import { classCounts, sampleClassOf, type SampleClass } from "./pointMarker";
import { useProjectionData } from "./useProjectionData";
import {
  EMPTY_SELECTION,
  experimentColor,
  experimentMetaById,
  isSelectionEmpty,
  selectionFromQuery,
  selectionToQuery,
  selectionWithoutDeadIds,
  totalPoints,
  type FacetSelection,
} from "./umapFacets";

interface UmapVisualizationProps {
  experimentId?: number;
  height?: number;
  /** If true, start with FOV mode (useful when no crops exist) */
  preferFovMode?: boolean;
}

/** Which message names each state. */
function panelCopy(viewMode: UmapType) {
  return {
    title: "title",
    loading: "loading",
    loadingHint: "loadingHint",
    computing: "computing",
    computingHint: "computingHint",
    computingPartial: "computingPartial",
    empty: "noEmbeddings",
    emptyHint: viewMode === "fov" ? "noEmbeddingsFov" : "noEmbeddingsCrops",
  };
}

export function UmapVisualization({
  experimentId,
  height = 500,
  preferFovMode = false,
}: UmapVisualizationProps): JSX.Element {
  const t = useTranslations("umap");
  const router = useRouter();
  const [viewMode, setViewMode] = useState<UmapType>(preferFovMode ? "fov" : "cropped");
  const [colorBy, setColorBy] = useState<ColorBy>("protein");

  // Only the dashboard's global plot round-trips its filter through the URL, so
  // a filtered view can be shared. On an experiment page the scope is the route
  // itself and writing facets into it would fight the page's own params.
  const syncsUrl = experimentId === undefined;
  const [selection, setSelection] = useState<FacetSelection>(() =>
    syncsUrl && typeof window !== "undefined"
      ? selectionFromQuery(window.location.search)
      : EMPTY_SELECTION
  );

  const queryClient = useQueryClient();

  useEffect(() => {
    if (!syncsUrl) return;
    const query = selectionToQuery(selection, window.location.search);
    // replaceState, not the router: this must not push history entries or
    // re-run the route's data fetching on every pill click.
    window.history.replaceState(
      null,
      "",
      query ? `${window.location.pathname}?${query}` : window.location.pathname
    );
  }, [selection, syncsUrl]);

  // These name the filter pills and the tooltip rows, and — for microscope and
  // PTM — colour the points whenever colour-by is set to them. A failure leaves
  // pills reading "#4" in grey and drops tooltip rows silently, which looks like
  // a lost backfill, so it has to be said out loud. (Protein points are
  // unaffected: they carry their own name and colour in the payload.)
  const { data: microscopes, isError: microscopesFailed } = useQuery({
    queryKey: ["microscopes"],
    queryFn: () => api.getMicroscopes(),
    staleTime: 1000 * 60 * 5,
  });
  const { data: proteins, isError: proteinsFailed } = useQuery({
    queryKey: ["proteins"],
    queryFn: () => api.getProteins(),
    staleTime: 1000 * 60 * 5,
  });
  const { data: ptms, isError: ptmsFailed } = useQuery({
    queryKey: ["ptms"],
    queryFn: () => api.getPtms(),
    staleTime: 1000 * 60 * 5,
  });
  const referencesFailed = microscopesFailed || proteinsFailed || ptmsFailed;

  // An experimentId prop scopes the plot; the user filters within it.
  const effectiveSelection = useMemo(
    () =>
      experimentId === undefined
        ? selection
        : { ...selection, experiment: [experimentId] },
    [selection, experimentId]
  );

  const { view, isLoading, isFetching, error, refetch } = useProjectionData({
    viewMode,
    selection: effectiveSelection,
    experimentId,
  });

  // A reference value the user has ticked can be deleted by anyone (reference
  // data is shared), and the backend then 404s the whole request. Drop only the
  // ids the error names, rather than leaving the plot stuck behind an error it
  // cannot explain — or throwing away the facets that are still valid.
  //
  // Say so when it happens. Repairing the filter silently would also erase it
  // from the URL, so someone opening a shared link to a filtered view could end
  // up looking at a wider selection believing it is the one they were sent.
  const [prunedFilterNotice, setPrunedFilterNotice] = useState<string | null>(null);
  useEffect(() => {
    const detail = error instanceof Error ? error.message : "";
    if (!detail) return;
    const pruned = selectionWithoutDeadIds(selection, detail);
    if (!pruned) return;
    console.warn("[UmapVisualization] dropped filter values the backend rejected:", detail);
    setPrunedFilterNotice((seen) =>
      seen?.includes(detail) ? seen : [seen, detail].filter(Boolean).join("; ")
    );
    setSelection(pruned);
  }, [error, selection]);

  const isRecomputing = view?.isComputing ?? false;
  const computeError = view?.computeError ?? null;
  const copy = panelCopy(viewMode);

  // The fit failed, so coordinates will never arrive on their own. BOTH backends
  // record the failure and stop rescheduling precisely so a poll cannot restart a
  // doomed multi-minute computation on a loop — which means a plain refetch
  // returns the recorded error forever and the button does nothing. Each has to
  // be asked explicitly.
  const [isRetrying, setIsRetrying] = useState(false);
  const handleRetryRefresh = useCallback(async () => {
    setIsRetrying(true);
    try {
      await api.triggerUmapRecomputation(viewMode);
      await queryClient.invalidateQueries({ queryKey: ["umap"] });
    } catch (e) {
      console.error("[UmapVisualization] Failed to trigger recomputation:", e);
    } finally {
      setIsRetrying(false);
    }
  }, [viewMode, queryClient]);

  // Handle click on a point - navigate to editor
  const handleChartClick = useCallback((state: { activePayload?: Array<{ payload: ProjectionPoint }> } | null) => {
    // Early return if click missed a point (expected behavior)
    if (!state?.activePayload?.[0]?.payload) return;

    const pointData = state.activePayload[0].payload;
    const expId = "experiment_id" in pointData ? pointData.experiment_id : experimentId;

    // Validate required IDs for navigation
    if (!expId) {
      console.error("[UmapVisualization] Cannot navigate: missing experiment_id", { pointData, experimentId });
      return;
    }
    if (!pointData.image_id) {
      console.error("[UmapVisualization] Cannot navigate: missing image_id", { pointData });
      return;
    }

    router.push(`/editor/${expId}/${pointData.image_id}`);
  }, [router, experimentId]);

  // The backend summarises the whole readable scope, since the panel must keep
  // offering a value after you untick it. On an experiment page that scope is
  // wider than the plot, so narrow it here — the summary is per experiment,
  // which is exactly the granularity that makes this a filter and not a second
  // request.
  const facetRows = useMemo(() => {
    const rows = view?.facets ?? [];
    return experimentId === undefined
      ? rows
      : rows.filter((row) => row.experiment_id === experimentId);
  }, [view?.facets, experimentId]);

  // Microscope and PTM live on the experiment, so points carry only
  // experiment_id and the rest is looked up here.
  const experimentMeta = useMemo(() => experimentMetaById(view?.facets ?? []), [view?.facets]);
  const microscopeById = useMemo(
    () => new Map((microscopes ?? []).map((m) => [m.id, m])),
    [microscopes]
  );
  const ptmById = useMemo(() => new Map((ptms ?? []).map((p) => [p.id, p])), [ptms]);

  const contextOf = useCallback(
    (point: ProjectionPoint): PointContext => {
      const meta = experimentMeta.get(point.experiment_id);
      const microscope = meta?.microscopeId ? microscopeById.get(meta.microscopeId) : undefined;
      const ptm = meta?.ptmId ? ptmById.get(meta.ptmId) : undefined;
      return {
        experimentName: meta?.name ?? `#${point.experiment_id}`,
        microscopeName: microscope?.name ?? null,
        // Full name, matching the legend: an abbreviation here and a name
        // there reads as two different PTMs on the same plot.
        ptmName: ptm?.name ?? null,
      };
    },
    [experimentMeta, microscopeById, ptmById]
  );

  /** The label and colour a point takes under the current colour-by dimension. */
  const styleOf = useCallback(
    (point: ProjectionPoint): { name: string; color: string } => {
      const meta = experimentMeta.get(point.experiment_id);

      switch (colorBy) {
        case "microscope": {
          const microscope = meta?.microscopeId
            ? microscopeById.get(meta.microscopeId)
            : undefined;
          return {
            name: microscope?.name ?? t("unassigned"),
            color: microscope?.color || DEFAULT_POINT_COLOR,
          };
        }
        case "ptm": {
          const ptm = meta?.ptmId ? ptmById.get(meta.ptmId) : undefined;
          return {
            name: ptm?.name ?? t("unassigned"),
            color: ptm?.color || DEFAULT_POINT_COLOR,
          };
        }
        case "experiment":
          return {
            name: meta?.name ?? `#${point.experiment_id}`,
            color: experimentColor(point.experiment_id),
          };
        case "protein":
        default:
          return {
            name: point.protein_name || t("unassigned"),
            color: point.protein_color || DEFAULT_POINT_COLOR,
          };
      }
    },
    [colorBy, experimentMeta, microscopeById, ptmById, t]
  );

  /**
   * PTM ids an experiment names but the cached reference list has never seen.
   *
   * Not a missing assignment — a row a colleague created after this tab loaded.
   * The `ptms` query has a 5-minute staleTime AND the app disables
   * `refetchOnWindowFocus`, so on a dashboard that mounts once it effectively
   * never refetches, while the projection query polls and its facets DO update.
   * Drawing those points plain claims "not a PTM", which is a claim we cannot
   * make, so it gets the same banner as a reference list that failed outright.
   */
  const [hasUnresolvedPtm, setHasUnresolvedPtm] = useState(false);
  const noteUnresolvedPtm = useCallback(() => setHasUnresolvedPtm(true), []);

  /**
   * Which sample class a point is: a PTM, its paired control, the unmodified
   * lattice, or an experiment nobody classified.
   *
   * Points carry only `experiment_id`; the rest is resolved by `sampleClassOf`,
   * which lives in `pointMarker.ts` so it is reachable by a test — in here the
   * whole composition could be replaced by `() => "none"` with 95 unit tests and
   * `tsc` still green, and the failure would hide its own evidence because a
   * single-class plot also removes its legend.
   */
  const classOfPoint = useCallback(
    (point: ProjectionPoint): SampleClass =>
      sampleClassOf(point.experiment_id, experimentMeta, ptmById, noteUnresolvedPtm),
    [experimentMeta, ptmById, noteUnresolvedPtm]
  );

  // recharts' `ActiveShape` is a union of call signatures, one of them taking
  // `unknown`, so contextual typing cannot pick one and the narrowing has to be
  // explicit. It casts to RechartsShapeProps only — the class is not something
  // recharts can supply, and `payload` is destructured out rather than spread so
  // omitting `cls` cannot type-check.
  //
  // ⚠️ `fill` is NOT in recharts' documented `ScatterPointItem`. It reaches the
  // shape only because `getComposedData` spreads the matching `<Cell>`'s props
  // into the point last — undocumented behaviour that every point's colour
  // depends on, and the first thing to check if a version bump greys the plot.
  const renderMarker = useCallback(
    (props: unknown) => {
      const { payload, ...geometry } = props as RechartsShapeProps;
      return (
        <ProjectionMarker
          {...geometry}
          cls={payload ? classOfPoint(payload) : "unrecorded"}
        />
      );
    },
    [classOfPoint]
  );

  // Counted from the same points and the same resolver the markers use, so the
  // key under the plot cannot describe a distinction the plot did not draw.
  const markerCounts = useMemo(
    () =>
      classCounts(
        (view?.points ?? []).map((point) => point.experiment_id),
        experimentMeta,
        ptmById
      ),
    [view?.points, experimentMeta, ptmById]
  );

  // Legend groups, derived from the same styleOf as the points themselves so a
  // swatch can never disagree with what is drawn.
  const legendGroups = useMemo(() => {
    if (!view?.points) return [];

    const groups = new Map<string, { name: string; color: string; count: number }>();
    view.points.forEach((point) => {
      const { name, color } = styleOf(point);
      if (!groups.has(name)) groups.set(name, { name, color, count: 0 });
      groups.get(name)!.count++;
    });

    return Array.from(groups.values()).sort((a, b) => b.count - a.count);
  }, [view?.points, styleOf]);

  const isFov = view?.isFov ?? viewMode === "fov";
  const totalCount = view?.totalCount ?? 0;
  const silhouetteScore = view?.silhouetteScore ?? null;

  // Error message parsing
  const errorMessage = error instanceof Error ? error.message : error ? t("unknownError") : null;
  const isNotEnoughData = errorMessage?.includes("Need at least") ?? false;
  const hasHardError = Boolean(error) && !view;

  // Log non-expected errors for debugging
  if (error && !isNotEnoughData) {
    console.error("[UmapVisualization] Failed to fetch projection data:", error);
  }

  // Render content based on state
  const renderContent = () => {
    if (isLoading) {
      return (
        <div
          className="flex flex-col items-center justify-center"
          style={{ height: height - 100 }}
        >
          <Spinner size="lg" />
          <span className="mt-3 text-text-secondary">
            {t(copy.loading)}
          </span>
          <span className="text-xs text-text-muted mt-1">
            {t(copy.loadingHint)}
          </span>
        </div>
      );
    }

    // Only take over the panel when there is nothing to show. A transient error
    // mid-poll must not blank a chart the user is already looking at.
    if (hasHardError) {
      return (
        <div
          className="flex flex-col items-center justify-center text-center"
          style={{ height: height - 100 }}
        >
          {isNotEnoughData ? (
            <Info className="w-12 h-12 text-accent-amber mb-4" />
          ) : (
            <AlertCircle className="w-12 h-12 text-accent-red mb-4" />
          )}
          <h3 className="text-lg font-semibold text-text-primary mb-2">
            {isNotEnoughData
              ? t("notEnoughData")
              : t("unableToGenerate")}
          </h3>
          <p className="text-text-secondary mb-4 max-w-md">{errorMessage}</p>
          {!isNotEnoughData && (
            <button
              onClick={() => refetch()}
              className="btn-secondary inline-flex items-center gap-2"
            >
              <RefreshCw className="w-4 h-4" />
              {t("retry")}
            </button>
          )}
        </div>
      );
    }

    // The background re-fit failed: nothing is coming, so say so instead of
    // spinning forever. Retry goes through the backend, which clears the
    // recorded failure and lets reads schedule refreshes again — a plain
    // refetch would return the recorded error forever.
    if (computeError && !view?.points.length) {
      return (
        <div
          className="flex flex-col items-center justify-center text-center"
          style={{ height: height - 100 }}
        >
          <AlertCircle className="w-12 h-12 text-accent-red mb-4" />
          <h3 className="text-lg font-semibold text-text-primary mb-2">
            {t("refreshFailed")}
          </h3>
          <p className="text-text-secondary mb-4 max-w-md">{t("refreshFailedHint")}</p>
          <button
            onClick={handleRetryRefresh}
            disabled={isRetrying}
            className="btn-secondary inline-flex items-center gap-2 disabled:opacity-50"
          >
            <RefreshCw className={`w-4 h-4 ${isRetrying ? "animate-spin" : ""}`} />
            {t("retry")}
          </button>
        </div>
      );
    }

    if (!view || view.points.length === 0) {
      // The filter excluded everything. Saying "upload and process images" here
      // would send the user to fix a problem they do not have.
      //
      // Gated on !isRecomputing: matching crops that have embeddings but no
      // coordinates yet also yield zero points, and blaming the filter for that
      // invites the user to throw away a filter that was never the problem.
      if (view && !isRecomputing && !isSelectionEmpty(selection)) {
        return (
          <div
            className="flex flex-col items-center justify-center text-center"
            style={{ height: height - 100 }}
          >
            <FilterX className="w-12 h-12 text-text-muted mb-4" />
            <h3 className="text-lg font-semibold text-text-primary mb-2">
              {t("noMatchingPoints")}
            </h3>
            <p className="text-text-secondary max-w-md mb-4">{t("noMatchingPointsHint")}</p>
            <button
              onClick={() => {
                setSelection(EMPTY_SELECTION);
                setPrunedFilterNotice(null);
              }}
              className="btn-secondary inline-flex items-center gap-2"
            >
              {t("clearAll")}
            </button>
          </div>
        );
      }

      // Nothing to plot yet, but a fit is running — the data is on its way,
      // so don't claim there are no embeddings (nor blame the filter).
      if (isRecomputing) {
        return (
          <div
            className="flex flex-col items-center justify-center text-center"
            style={{ height: height - 100 }}
          >
            <Spinner size="lg" />
            <h3 className="mt-3 text-lg font-semibold text-text-primary">
              {t(copy.computing)}
            </h3>
            <p className="text-text-secondary max-w-md">{t(copy.computingHint)}</p>
          </div>
        );
      }

      return (
        <div
          className="flex flex-col items-center justify-center text-center"
          style={{ height: height - 100 }}
        >
          <Info className="w-12 h-12 text-text-muted mb-4" />
          <h3 className="text-lg font-semibold text-text-primary mb-2">
            {t(copy.empty)}
          </h3>
          <p className="text-text-secondary max-w-md">{t(copy.emptyHint)}</p>
        </div>
      );
    }

    // Success - render chart
    return (
      <>
        {/* Some points are plotted, but newer ones have no coordinates yet. */}
        {isRecomputing && (
          <div className="flex items-center gap-2 mb-3 px-3 py-2 rounded-md bg-accent-amber/10 border border-accent-amber/30">
            <Spinner size="sm" />
            <span className="text-xs text-text-secondary">
              {t(copy.computingPartial)}
            </span>
          </div>
        )}
        {/* Points are plotted, but the re-fit for the newer ones failed. */}
        {computeError && (
          <div className="flex items-center gap-2 mb-3 px-3 py-2 rounded-md bg-accent-red/10 border border-accent-red/30">
            <AlertCircle className="w-4 h-4 text-accent-red flex-shrink-0" />
            <span className="text-xs text-text-secondary flex-1">
              {t("refreshFailedPartial")}
            </span>
            <button
              onClick={handleRetryRefresh}
              disabled={isRetrying}
              className="text-xs underline text-text-secondary hover:text-text-primary disabled:opacity-50"
            >
              {t("retry")}
            </button>
          </div>
        )}
        <div style={{ height: height - 100 }}>
          <ResponsiveContainer width="100%" height="100%">
            <ScatterChart
              margin={{ top: 20, right: 20, bottom: 20, left: 20 }}
              onClick={handleChartClick}
            >
              <XAxis
                type="number"
                dataKey="x"
                name={"UMAP 1"}
                tick={UMAP_AXIS_STYLE.tick}
                axisLine={UMAP_AXIS_STYLE.axisLine}
                tickLine={UMAP_AXIS_STYLE.tickLine}
                domain={UMAP_AXIS_DOMAIN}
                tickFormatter={formatAxisTick}
              />
              <YAxis
                type="number"
                dataKey="y"
                name={"UMAP 2"}
                tick={UMAP_AXIS_STYLE.tick}
                axisLine={UMAP_AXIS_STYLE.axisLine}
                tickLine={UMAP_AXIS_STYLE.tickLine}
                domain={UMAP_AXIS_DOMAIN}
                tickFormatter={formatAxisTick}
              />
              <ZAxis range={[60, 60]} />
              <Tooltip
                content={
                  isFov ? (
                    <FovTooltip t={t} contextOf={contextOf} />
                  ) : (
                    <CroppedTooltip t={t} contextOf={contextOf} />
                  )
                }
                cursor={UMAP_TOOLTIP_CURSOR}
              />
              <Scatter
                data={view.points}
                shape={renderMarker}
                {...UMAP_SCATTER_ANIMATION}
              >
                {view.points.map((point, index) => (
                  // Colour only. Opacity, stroke and the centre dot belong to
                  // the marker; splitting them across both would give one point
                  // two places to disagree with itself.
                  <Cell key={`cell-${index}`} fill={styleOf(point).color} />
                ))}
              </Scatter>
            </ScatterChart>
          </ResponsiveContainer>
        </div>

        <ProjectionLegend groups={legendGroups} />
        <MarkerLegend counts={markerCounts} t={t} />
      </>
    );
  };

  return (
    <div className="glass-card p-4">
      {/* Header with Toggle - ALWAYS VISIBLE */}
      <div className="flex items-center justify-between mb-4">
        <div>
          <h3 className="font-display font-semibold text-text-primary">
            {t(copy.title)}
          </h3>
          {view && (
            <div className="flex items-center gap-3 text-sm text-text-secondary">
              <span>
                {totalCount.toLocaleString()} {isFov ? t("fovImages") : t("cellCrops")}
              </span>
              {silhouetteScore !== null && (
                <span
                  className={`px-2 py-0.5 rounded text-xs font-mono ${getSilhouetteScoreStyle(silhouetteScore)}`}
                  title={t("silhouetteTooltip")}
                >
                  {t("silhouette")}: {silhouetteScore.toFixed(3)}
                </span>
              )}
            </div>
          )}
        </div>

        <div className="flex items-center gap-3">
          {/* FOV/Cropped */}
          <div className="flex items-center bg-bg-secondary rounded-lg p-1">
            <button
              onClick={() => setViewMode("fov")}
              className={`px-3 py-1.5 rounded-md text-sm font-medium transition-colors flex items-center gap-1.5 ${
                viewMode === "fov"
                  ? "bg-primary-500 text-white"
                  : "text-text-secondary hover:text-text-primary"
              }`}
              title={t("fovTooltip")}
            >
              <Grid className="w-4 h-4" />
              {t("fov")}
            </button>
            <button
              onClick={() => setViewMode("cropped")}
              className={`px-3 py-1.5 rounded-md text-sm font-medium transition-colors flex items-center gap-1.5 ${
                viewMode === "cropped"
                  ? "bg-primary-500 text-white"
                  : "text-text-secondary hover:text-text-primary"
              }`}
              title={t("croppedTooltip")}
            >
              <Layers className="w-4 h-4" />
              {t("cropped")}
            </button>
          </div>

          {/* Refresh button */}
          <button
            onClick={() => {
              refetch();
              // Also the reference lists: the referencesFailed banner tells the
              // user to refresh, and refetching only the plot leaves the names
              // it complains about exactly as wrong as they were.
              queryClient.invalidateQueries({ queryKey: ["microscopes"] });
              queryClient.invalidateQueries({ queryKey: ["proteins"] });
              queryClient.invalidateQueries({ queryKey: ["ptms"] });
            }}
            disabled={isFetching}
            className="p-2 hover:bg-white/5 rounded-lg transition-colors disabled:opacity-50"
            title={t("refresh")}
          >
            <RefreshCw
              className={`w-4 h-4 text-text-secondary ${isFetching ? "animate-spin" : ""}`}
            />
          </button>
        </div>
      </div>

      {/* A reference list failed to load, so names and colours are wrong rather
          than missing — the plot looks like a lost backfill if we stay quiet. */}
      {(referencesFailed || hasUnresolvedPtm) && (
        <div className="flex items-center gap-2 mb-3 px-3 py-2 rounded-md bg-accent-amber/10 border border-accent-amber/30">
          <AlertCircle className="w-4 h-4 text-accent-amber flex-shrink-0" />
          <span className="text-xs text-text-secondary">{t("referencesFailed")}</span>
        </div>
      )}

      {/* Filter values were dropped for us; say which, so a shared link that
          quietly widened is not mistaken for the view it was sent as. */}
      {prunedFilterNotice && (
        <div className="flex items-center gap-2 mb-3 px-3 py-2 rounded-md bg-accent-amber/10 border border-accent-amber/30">
          <FilterX className="w-4 h-4 text-accent-amber flex-shrink-0" />
          <span className="text-xs text-text-secondary flex-1">
            {t("filterValuesDropped", { detail: prunedFilterNotice })}
          </span>
          <button
            onClick={() => setPrunedFilterNotice(null)}
            className="text-xs underline text-text-secondary hover:text-text-primary"
          >
            {t("dismiss")}
          </button>
        </div>
      )}

      {/* Advanced filter — needs the facet summary, which arrives with the data */}
      {view && (
        <UmapFilterPanel
          rows={facetRows}
          selection={selection}
          onSelectionChange={setSelection}
          colorBy={colorBy}
          onColorByChange={setColorBy}
          microscopes={microscopes}
          proteins={proteins}
          ptms={ptms}
          showExperimentFacet={experimentId === undefined}
          shownCount={view.points.length}
          totalCount={totalPoints(facetRows)}
        />
      )}

      {/* Content area */}
      {renderContent()}
    </div>
  );
}
