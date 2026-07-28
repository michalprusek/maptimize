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
  TooltipProps,
} from "recharts";
import {
  api,
  UmapPoint,
  UmapFovPoint,
  UmapDataResponse,
  UmapFovDataResponse,
  UmapType,
  API_URL,
} from "@/lib/api";
import { Spinner, MicroscopyImage } from "@/components/ui";
import { RefreshCw, Info, AlertCircle, Grid, Layers, FilterX } from "lucide-react";
import {
  DEFAULT_POINT_COLOR,
  UMAP_AXIS_STYLE,
  UMAP_AXIS_DOMAIN,
  UMAP_TOOLTIP_CURSOR,
  UMAP_SCATTER_ANIMATION,
  UMAP_STALE_POLL_MS,
  formatAxisTick,
  getSilhouetteScoreStyle,
} from "./chartConfig";
import { UmapFilterPanel, type ColorBy } from "./UmapFilterPanel";
import {
  EMPTY_SELECTION,
  experimentColor,
  experimentMetaById,
  isSelectionEmpty,
  selectionFromQuery,
  selectionKey,
  selectionToQuery,
  totalPoints,
  type FacetSelection,
} from "./umapFacets";

interface UmapVisualizationProps {
  experimentId?: number;
  height?: number;
  /** If true, start with FOV mode (useful when no crops exist) */
  preferFovMode?: boolean;
}

/** Build authenticated URL by appending token as query parameter */
function buildAuthenticatedUrl(thumbnailUrl: string): string {
  const token = api.getToken();
  const separator = thumbnailUrl.includes("?") ? "&" : "?";
  return `${API_URL}${thumbnailUrl}${separator}token=${token}`;
}

/** Hide element on image load error */
function hideOnError(e: React.SyntheticEvent<HTMLImageElement>): void {
  e.currentTarget.style.display = "none";
}

/** The acquisition context of a point, resolved from the facet summary. */
interface PointContext {
  experimentName: string;
  microscopeName: string | null;
  ptmName: string | null;
}

/** The rows shared by both tooltips: where this point came from. */
function ContextRows({
  context,
  t,
}: {
  context: PointContext;
  t: (key: string, values?: Record<string, string | number>) => string;
}): JSX.Element {
  return (
    <>
      <div className="text-xs text-text-secondary truncate">{context.experimentName}</div>
      {context.microscopeName && (
        <div className="text-xs text-text-muted truncate">
          {t("facetMicroscope")}: {context.microscopeName}
        </div>
      )}
      {context.ptmName && (
        <div className="text-xs text-text-muted truncate">
          {t("facetPtm")}: {context.ptmName}
        </div>
      )}
    </>
  );
}

// Tooltip for cropped cell view
interface CroppedTooltipProps extends TooltipProps<number, string> {
  t: (key: string, values?: Record<string, string | number>) => string;
  contextOf: (point: UmapPoint | UmapFovPoint) => PointContext;
}

function CroppedTooltip({
  active,
  payload,
  t,
  contextOf,
}: CroppedTooltipProps): JSX.Element | null {
  if (!active || !payload || !payload.length) return null;

  const point = payload[0].payload as UmapPoint;

  return (
    <div className="bg-bg-elevated border border-white/10 rounded-lg shadow-xl p-3 max-w-[220px]">
      <MicroscopyImage
        src={buildAuthenticatedUrl(point.thumbnail_url)}
        alt="Cell crop"
        className="w-full h-32 object-contain rounded mb-2 bg-black/50"
        onError={hideOnError}
      />
      <div className="space-y-1">
        <div
          className="font-medium text-text-primary flex items-center gap-2"
          style={{ color: point.protein_color }}
        >
          <span
            className="w-2.5 h-2.5 rounded-full flex-shrink-0"
            style={{ backgroundColor: point.protein_color }}
          />
          {point.protein_name || t("unassigned")}
        </div>
        <ContextRows context={contextOf(point)} t={t} />
        {point.bundleness_score !== null && (
          <div className="text-xs text-text-secondary">
            {t("bundleness")}: {point.bundleness_score.toFixed(2)}
          </div>
        )}
        <div className="text-xs text-text-muted">{t("cropId", { id: point.crop_id })}</div>
      </div>
    </div>
  );
}

// Tooltip for FOV view
interface FovTooltipProps extends TooltipProps<number, string> {
  t: (key: string, values?: Record<string, string | number>) => string;
  contextOf: (point: UmapPoint | UmapFovPoint) => PointContext;
}

function FovTooltip({
  active,
  payload,
  t,
  contextOf,
}: FovTooltipProps): JSX.Element | null {
  if (!active || !payload || !payload.length) return null;

  const point = payload[0].payload as UmapFovPoint;

  return (
    <div className="bg-bg-elevated border border-white/10 rounded-lg shadow-xl p-3 max-w-[250px]">
      <MicroscopyImage
        src={buildAuthenticatedUrl(point.thumbnail_url)}
        alt="FOV thumbnail"
        className="w-full h-40 object-contain rounded mb-2 bg-black/50"
        onError={hideOnError}
      />
      <div className="space-y-1">
        <div className="font-medium text-text-primary truncate text-sm">
          {point.original_filename}
        </div>
        <div
          className="text-sm flex items-center gap-2"
          style={{ color: point.protein_color }}
        >
          <span
            className="w-2.5 h-2.5 rounded-full flex-shrink-0"
            style={{ backgroundColor: point.protein_color }}
          />
          {point.protein_name || t("unassigned")}
        </div>
        <ContextRows context={contextOf(point)} t={t} />
        <div className="text-xs text-text-muted">{t("imageId", { id: point.image_id })}</div>
      </div>
    </div>
  );
}

// Type guard to check if response is FOV type
function isFovResponse(
  data: UmapDataResponse | UmapFovDataResponse
): data is UmapFovDataResponse {
  return "total_images" in data;
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
    const query = selectionToQuery(selection);
    // replaceState, not the router: this must not push history entries or
    // re-run the route's data fetching on every pill click.
    window.history.replaceState(
      null,
      "",
      query ? `${window.location.pathname}?${query}` : window.location.pathname
    );
  }, [selection, syncsUrl]);

  const { data: microscopes } = useQuery({
    queryKey: ["microscopes"],
    queryFn: () => api.getMicroscopes(),
    staleTime: 1000 * 60 * 5,
  });
  const { data: proteins } = useQuery({
    queryKey: ["proteins"],
    queryFn: () => api.getProteins(),
    staleTime: 1000 * 60 * 5,
  });
  const { data: ptms } = useQuery({
    queryKey: ["ptms"],
    queryFn: () => api.getPtms(),
    staleTime: 1000 * 60 * 5,
  });

  // An experimentId prop scopes the plot; the user filters within it.
  const effectiveSelection = useMemo(
    () =>
      experimentId === undefined
        ? selection
        : { ...selection, experiment: [experimentId] },
    [selection, experimentId]
  );

  const { data, isLoading, error, refetch, isFetching } = useQuery({
    queryKey: ["umap", experimentId, viewMode, selectionKey(effectiveSelection)],
    queryFn: () => api.getUmapData({ umapType: viewMode, selection: effectiveSelection }),
    staleTime: 1000 * 60 * 5, // Cache for 5 minutes
    retry: false,
    // New uploads/edits arrive without coordinates; the request that observes
    // that schedules a background re-fit. Poll until those coordinates land.
    refetchInterval: (query) =>
      query.state.data?.is_stale ? UMAP_STALE_POLL_MS : false,
  });

  // A reference value the user has ticked can be deleted by anyone (reference
  // data is shared), and the backend then 404s the whole request. Drop the dead
  // ids rather than leaving the plot stuck behind an error it cannot explain.
  useEffect(() => {
    const detail = error instanceof Error ? error.message : "";
    if (!detail.includes("not found") || isSelectionEmpty(selection)) return;
    setSelection(EMPTY_SELECTION);
  }, [error, selection]);

  const isRecomputing = data?.is_stale ?? false;
  const refreshError = data?.refresh_error ?? null;

  // The re-fit failed, so coordinates will never arrive on their own. Ask the
  // backend to retry (which clears the recorded failure) and resume polling.
  const [isRetrying, setIsRetrying] = useState(false);
  const handleRetryRefresh = useCallback(async () => {
    setIsRetrying(true);
    try {
      await api.triggerUmapRecomputation(viewMode);
      await queryClient.invalidateQueries({ queryKey: ["umap"] });
    } catch (e) {
      console.error("[UmapVisualization] Failed to trigger UMAP recomputation:", e);
    } finally {
      setIsRetrying(false);
    }
  }, [viewMode, queryClient]);

  // Handle click on UMAP point - navigate to editor
  const handleChartClick = useCallback((state: { activePayload?: Array<{ payload: UmapPoint | UmapFovPoint }> } | null) => {
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
    const rows = data?.facets ?? [];
    return experimentId === undefined
      ? rows
      : rows.filter((row) => row.experiment_id === experimentId);
  }, [data?.facets, experimentId]);

  // Microscope and PTM live on the experiment, so points carry only
  // experiment_id and the rest is looked up here.
  const experimentMeta = useMemo(() => experimentMetaById(data?.facets ?? []), [data?.facets]);
  const microscopeById = useMemo(
    () => new Map((microscopes ?? []).map((m) => [m.id, m])),
    [microscopes]
  );
  const ptmById = useMemo(() => new Map((ptms ?? []).map((p) => [p.id, p])), [ptms]);

  const contextOf = useCallback(
    (point: UmapPoint | UmapFovPoint): PointContext => {
      const meta = experimentMeta.get(point.experiment_id);
      const microscope = meta?.microscopeId ? microscopeById.get(meta.microscopeId) : undefined;
      const ptm = meta?.ptmId ? ptmById.get(meta.ptmId) : undefined;
      return {
        experimentName: meta?.name ?? `#${point.experiment_id}`,
        microscopeName: microscope?.name ?? null,
        ptmName: ptm?.abbreviation || ptm?.name || null,
      };
    },
    [experimentMeta, microscopeById, ptmById]
  );

  /** The label and colour a point takes under the current colour-by dimension. */
  const styleOf = useCallback(
    (point: UmapPoint | UmapFovPoint): { name: string; color: string } => {
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

  // Legend groups, derived from the same styleOf as the points themselves so a
  // swatch can never disagree with what is drawn.
  const legendGroups = useMemo(() => {
    if (!data?.points) return [];

    const groups = new Map<string, { name: string; color: string; count: number }>();
    data.points.forEach((point) => {
      const { name, color } = styleOf(point);
      if (!groups.has(name)) groups.set(name, { name, color, count: 0 });
      groups.get(name)!.count++;
    });

    return Array.from(groups.values()).sort((a, b) => b.count - a.count);
  }, [data?.points, styleOf]);

  // Prepare data for rendering (may be null/undefined)
  const isFov = data ? isFovResponse(data) : viewMode === "fov";
  const totalCount = data
    ? (isFovResponse(data) ? data.total_images : data.total_crops)
    : 0;
  const silhouetteScore = data?.silhouette_score ?? null;

  // Error message parsing
  const errorMessage = error instanceof Error ? error.message : error ? t("unknownError") : null;
  const isNotEnoughData = errorMessage?.includes("Need at least") ?? false;

  // Log non-expected errors for debugging
  if (error && !isNotEnoughData) {
    console.error("[UmapVisualization] Failed to fetch UMAP data:", error);
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
            {t("loading")}
          </span>
          <span className="text-xs text-text-muted mt-1">
            {t("loadingHint")}
          </span>
        </div>
      );
    }

    // Only take over the panel when there is nothing to show. A transient error
    // mid-poll must not blank a chart the user is already looking at.
    if (error && !data) {
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
    // recorded failure and lets reads schedule refreshes again.
    if (refreshError && !data?.points.length) {
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

    if (!data || data.points.length === 0) {
      // The filter excluded everything. Saying "upload and process images" here
      // would send the user to fix a problem they do not have.
      if (data && !isSelectionEmpty(selection)) {
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
              onClick={() => setSelection(EMPTY_SELECTION)}
              className="btn-secondary inline-flex items-center gap-2"
            >
              {t("clearAll")}
            </button>
          </div>
        );
      }

      // Nothing to plot yet, but a re-fit is running — the data is on its way,
      // so don't claim there are no embeddings.
      if (isRecomputing) {
        return (
          <div
            className="flex flex-col items-center justify-center text-center"
            style={{ height: height - 100 }}
          >
            <Spinner size="lg" />
            <h3 className="mt-3 text-lg font-semibold text-text-primary">
              {t("computing")}
            </h3>
            <p className="text-text-secondary max-w-md">{t("computingHint")}</p>
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
            {t("noEmbeddings")}
          </h3>
          <p className="text-text-secondary max-w-md">
            {viewMode === "fov"
              ? t("noEmbeddingsFov")
              : t("noEmbeddingsCrops")}
          </p>
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
              {t("computingPartial")}
            </span>
          </div>
        )}
        {/* Points are plotted, but the re-fit for the newer ones failed. */}
        {refreshError && (
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
                name="UMAP 1"
                tick={UMAP_AXIS_STYLE.tick}
                axisLine={UMAP_AXIS_STYLE.axisLine}
                tickLine={UMAP_AXIS_STYLE.tickLine}
                domain={UMAP_AXIS_DOMAIN}
                tickFormatter={formatAxisTick}
              />
              <YAxis
                type="number"
                dataKey="y"
                name="UMAP 2"
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
                data={data.points}
                {...UMAP_SCATTER_ANIMATION}
              >
                {data.points.map((point, index) => (
                  <Cell
                    key={`cell-${index}`}
                    fill={styleOf(point).color}
                    fillOpacity={0.75}
                    stroke="rgba(255,255,255,0.3)"
                    strokeWidth={1}
                    cursor="pointer"
                  />
                ))}
              </Scatter>
            </ScatterChart>
          </ResponsiveContainer>
        </div>

        {/* Legend */}
        <div className="flex flex-wrap gap-3 mt-4 pt-4 border-t border-white/5">
          {legendGroups.map((group) => (
            <div
              key={group.name}
              className="flex items-center gap-1.5 px-2 py-1 rounded bg-white/5"
            >
              <div
                className="w-3 h-3 rounded-full"
                style={{ backgroundColor: group.color }}
              />
              <span className="text-xs text-text-secondary">
                {group.name}{" "}
                <span className="text-text-muted">({group.count})</span>
              </span>
            </div>
          ))}
        </div>
      </>
    );
  };

  return (
    <div className="glass-card p-4">
      {/* Header with Toggle - ALWAYS VISIBLE */}
      <div className="flex items-center justify-between mb-4">
        <div>
          <h3 className="font-display font-semibold text-text-primary">
            {t("title")}
          </h3>
          {data && (
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
          {/* Toggle Buttons */}
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
            onClick={() => refetch()}
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

      {/* Advanced filter — needs the facet summary, which arrives with the data */}
      {data && (
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
          shownCount={data.points.length}
          totalCount={totalPoints(facetRows)}
        />
      )}

      {/* Content area */}
      {renderContent()}
    </div>
  );
}
