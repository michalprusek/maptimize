"use client";

import { useMemo } from "react";
import { useQuery } from "@tanstack/react-query";
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
import { api, UmapPoint, API_URL } from "@/lib/api";
import { Spinner } from "@/components/ui";
import { RefreshCw, Info, AlertCircle } from "lucide-react";

interface UmapVisualizationProps {
  experimentId?: number;
  height?: number;
}

const DEFAULT_COLOR = "#888888";

function CustomTooltip({
  active,
  payload,
}: TooltipProps<number, string>): JSX.Element | null {
  if (!active || !payload || !payload.length) return null;

  const point = payload[0].payload as UmapPoint;
  const token = api.getToken();
  const imageUrl = `${API_URL}${point.thumbnail_url}&token=${token}`;

  return (
    <div className="bg-bg-elevated border border-white/10 rounded-lg shadow-xl p-3 max-w-[200px]">
      <img
        src={imageUrl}
        alt="Cell crop"
        className="w-full h-32 object-contain rounded mb-2 bg-black/50"
        onError={(e) => {
          (e.target as HTMLImageElement).src = "/placeholder-cell.png";
        }}
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
          {point.protein_name || "Unassigned"}
        </div>
        {point.bundleness_score !== null && (
          <div className="text-xs text-text-secondary">
            Bundleness: {point.bundleness_score.toFixed(2)}
          </div>
        )}
        <div className="text-xs text-text-muted">Crop #{point.crop_id}</div>
      </div>
    </div>
  );
}

export function UmapVisualization({
  experimentId,
  height = 500,
}: UmapVisualizationProps): JSX.Element {
  const { data, isLoading, error, refetch, isFetching } = useQuery({
    queryKey: ["umap", experimentId],
    queryFn: () => api.getUmapData(experimentId),
    staleTime: 1000 * 60 * 5, // Cache for 5 minutes
    retry: false,
  });

  // Group points by protein for legend
  const proteinGroups = useMemo(() => {
    if (!data?.points) return [];

    const groups = new Map<
      string,
      { name: string; color: string; count: number }
    >();

    data.points.forEach((point) => {
      const name = point.protein_name || "Unassigned";
      const color = point.protein_color || DEFAULT_COLOR;

      if (!groups.has(name)) {
        groups.set(name, { name, color, count: 0 });
      }
      groups.get(name)!.count++;
    });

    return Array.from(groups.values()).sort((a, b) => b.count - a.count);
  }, [data?.points]);

  if (isLoading) {
    return (
      <div
        className="glass-card p-8 flex flex-col items-center justify-center"
        style={{ height }}
      >
        <Spinner size="lg" />
        <span className="mt-3 text-text-secondary">
          Computing UMAP projection...
        </span>
        <span className="text-xs text-text-muted mt-1">
          This may take a moment for large datasets
        </span>
      </div>
    );
  }

  if (error) {
    const errorMessage =
      error instanceof Error ? error.message : "Unknown error";
    const isNotEnoughData = errorMessage.includes("Need at least");

    return (
      <div
        className="glass-card p-8 flex flex-col items-center justify-center text-center"
        style={{ height }}
      >
        {isNotEnoughData ? (
          <Info className="w-12 h-12 text-accent-amber mb-4" />
        ) : (
          <AlertCircle className="w-12 h-12 text-accent-red mb-4" />
        )}
        <h3 className="text-lg font-semibold text-text-primary mb-2">
          {isNotEnoughData
            ? "Not Enough Data"
            : "Unable to Generate Visualization"}
        </h3>
        <p className="text-text-secondary mb-4 max-w-md">{errorMessage}</p>
        <button
          onClick={() => refetch()}
          className="btn-secondary inline-flex items-center gap-2"
        >
          <RefreshCw className="w-4 h-4" />
          Retry
        </button>
      </div>
    );
  }

  if (!data || data.points.length === 0) {
    return (
      <div
        className="glass-card p-8 flex flex-col items-center justify-center text-center"
        style={{ height }}
      >
        <Info className="w-12 h-12 text-text-muted mb-4" />
        <h3 className="text-lg font-semibold text-text-primary mb-2">
          No Embeddings Available
        </h3>
        <p className="text-text-secondary max-w-md">
          Upload and process images to generate DINOv3 feature embeddings for
          visualization.
        </p>
      </div>
    );
  }

  return (
    <div className="glass-card p-4">
      {/* Header */}
      <div className="flex items-center justify-between mb-4">
        <div>
          <h3 className="font-display font-semibold text-text-primary">
            Feature Space (UMAP)
          </h3>
          <p className="text-sm text-text-secondary">
            {data.total_crops.toLocaleString()} cell crops visualized
          </p>
        </div>

        <button
          onClick={() => refetch()}
          disabled={isFetching}
          className="p-2 hover:bg-white/5 rounded-lg transition-colors disabled:opacity-50"
          title="Refresh"
        >
          <RefreshCw
            className={`w-4 h-4 text-text-secondary ${isFetching ? "animate-spin" : ""}`}
          />
        </button>
      </div>

      {/* Chart */}
      <div style={{ height: height - 100 }}>
        <ResponsiveContainer width="100%" height="100%">
          <ScatterChart margin={{ top: 20, right: 20, bottom: 20, left: 20 }}>
            <XAxis
              type="number"
              dataKey="x"
              name="UMAP 1"
              tick={{ fill: "#5a7285", fontSize: 10 }}
              axisLine={{ stroke: "#2a3a4a" }}
              tickLine={{ stroke: "#2a3a4a" }}
              domain={["dataMin - 1", "dataMax + 1"]}
              tickFormatter={(value) => Math.round(value).toString()}
            />
            <YAxis
              type="number"
              dataKey="y"
              name="UMAP 2"
              tick={{ fill: "#5a7285", fontSize: 10 }}
              axisLine={{ stroke: "#2a3a4a" }}
              tickLine={{ stroke: "#2a3a4a" }}
              domain={["dataMin - 1", "dataMax + 1"]}
              tickFormatter={(value) => Math.round(value).toString()}
            />
            <ZAxis range={[40, 40]} />
            <Tooltip
              content={<CustomTooltip />}
              cursor={{ strokeDasharray: "3 3", stroke: "#5a7285" }}
            />
            <Scatter data={data.points} isAnimationActive={false}>
              {data.points.map((point, index) => (
                <Cell
                  key={`cell-${index}`}
                  fill={point.protein_color || DEFAULT_COLOR}
                  fillOpacity={0.75}
                  stroke="rgba(255,255,255,0.3)"
                  strokeWidth={0.5}
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
    </div>
  );
}
