"use client";

import { useState, useMemo, useCallback } from "react";
import { useQuery } from "@tanstack/react-query";
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
import { RefreshCw, Info, AlertCircle, Grid, Layers } from "lucide-react";
import {
  DEFAULT_POINT_COLOR,
  UMAP_AXIS_STYLE,
  UMAP_AXIS_DOMAIN,
  UMAP_TOOLTIP_CURSOR,
  UMAP_SCATTER_ANIMATION,
  formatAxisTick,
  getSilhouetteScoreStyle,
} from "./chartConfig";

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

// Tooltip for cropped cell view
interface CroppedTooltipProps extends TooltipProps<number, string> {
  t: (key: string, values?: Record<string, string | number>) => string;
}

function CroppedTooltip({
  active,
  payload,
  t,
}: CroppedTooltipProps): JSX.Element | null {
  if (!active || !payload || !payload.length) return null;

  const point = payload[0].payload as UmapPoint;

  return (
    <div className="bg-bg-elevated border border-white/10 rounded-lg shadow-xl p-3 max-w-[200px]">
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
}

function FovTooltip({
  active,
  payload,
  t,
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

  const { data, isLoading, error, refetch, isFetching } = useQuery({
    queryKey: ["umap", experimentId, viewMode],
    queryFn: () => api.getUmapData(experimentId, viewMode),
    staleTime: 1000 * 60 * 5, // Cache for 5 minutes
    retry: false,
  });

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

  // Group points by protein for legend
  const proteinGroups = useMemo(() => {
    if (!data?.points) return [];

    const groups = new Map<
      string,
      { name: string; color: string; count: number }
    >();

    data.points.forEach((point) => {
      const name = point.protein_name || t("unassigned");
      const color = point.protein_color || DEFAULT_POINT_COLOR;

      if (!groups.has(name)) {
        groups.set(name, { name, color, count: 0 });
      }
      groups.get(name)!.count++;
    });

    return Array.from(groups.values()).sort((a, b) => b.count - a.count);
  }, [data?.points, t]);

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

    if (error) {
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

    if (!data || data.points.length === 0) {
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
                content={isFov ? <FovTooltip t={t} /> : <CroppedTooltip t={t} />}
                cursor={UMAP_TOOLTIP_CURSOR}
              />
              <Scatter
                data={data.points}
                {...UMAP_SCATTER_ANIMATION}
              >
                {data.points.map((point, index) => (
                  <Cell
                    key={`cell-${index}`}
                    fill={point.protein_color || DEFAULT_POINT_COLOR}
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
          {proteinGroups.map((group) => (
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

      {/* Content area */}
      {renderContent()}
    </div>
  );
}
