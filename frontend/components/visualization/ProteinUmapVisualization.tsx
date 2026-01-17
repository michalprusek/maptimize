"use client";

import { useMemo } from "react";
import { useQuery } from "@tanstack/react-query";
import { useTranslations } from "next-intl";
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
import { api, UmapProteinPoint } from "@/lib/api";
import { Spinner } from "@/components/ui";
import { RefreshCw, Info, Dna } from "lucide-react";

interface ProteinUmapVisualizationProps {
  height?: number;
}

const DEFAULT_COLOR = "#888888";

interface ProteinTooltipProps extends TooltipProps<number, string> {
  t: (key: string, values?: Record<string, string | number>) => string;
}

function ProteinTooltip({ active, payload, t }: ProteinTooltipProps): JSX.Element | null {
  if (!active || !payload || !payload.length) return null;

  const point = payload[0].payload as UmapProteinPoint;

  return (
    <div className="bg-bg-elevated border border-white/10 rounded-lg shadow-xl p-3 max-w-[200px]">
      <div className="flex items-center gap-2 mb-2">
        <span
          className="w-3 h-3 rounded-full flex-shrink-0"
          style={{ backgroundColor: point.color }}
        />
        <span className="font-medium text-text-primary">{point.name}</span>
      </div>
      <div className="space-y-1 text-xs text-text-secondary">
        {point.sequence_length && (
          <div>
            {t("sequenceLengthShort")}: {point.sequence_length} {t("aminoAcids")}
          </div>
        )}
        <div>
          {t("images")}: {point.image_count}
        </div>
      </div>
    </div>
  );
}

export function ProteinUmapVisualization({
  height = 400,
}: ProteinUmapVisualizationProps): JSX.Element {
  const t = useTranslations("proteinsPage");

  const { data, isLoading, refetch, isFetching } = useQuery({
    queryKey: ["protein-umap"],
    queryFn: () => api.getProteinUmap(),
    staleTime: 1000 * 60 * 5,
    retry: false,
  });

  // Group points by protein for legend
  const proteinGroups = useMemo(() => {
    if (!data?.points) return [];

    return data.points.map((point) => ({
      name: point.name,
      color: point.color || DEFAULT_COLOR,
      count: point.image_count,
    }));
  }, [data?.points]);

  const renderContent = () => {
    if (isLoading) {
      return (
        <div
          className="flex flex-col items-center justify-center"
          style={{ height: height - 100 }}
        >
          <Spinner size="lg" />
          <span className="mt-3 text-text-secondary">Loading...</span>
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
            {t("noUmapData")}
          </h3>
          <p className="text-text-secondary max-w-md">
            {t("noUmapDataDesc")}
          </p>
        </div>
      );
    }

    return (
      <>
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
              <ZAxis range={[100, 100]} />
              <Tooltip
                content={<ProteinTooltip t={t} />}
                cursor={{ strokeDasharray: "3 3", stroke: "#5a7285" }}
              />
              <Scatter
                data={data.points}
                isAnimationActive={true}
                animationDuration={300}
                animationEasing="ease-out"
              >
                {data.points.map((point, index) => (
                  <Cell
                    key={`cell-${index}`}
                    fill={point.color || DEFAULT_COLOR}
                    fillOpacity={0.85}
                    stroke="rgba(255,255,255,0.4)"
                    strokeWidth={2}
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
                {group.name}
                {group.count > 0 && (
                  <span className="text-text-muted ml-1">({group.count} img)</span>
                )}
              </span>
            </div>
          ))}
        </div>
      </>
    );
  };

  return (
    <div className="glass-card p-4">
      {/* Header */}
      <div className="flex items-center justify-between mb-4">
        <div>
          <div className="flex items-center gap-2">
            <Dna className="w-5 h-5 text-primary-400" />
            <h3 className="font-display font-semibold text-text-primary">
              {t("umapTitle")}
            </h3>
          </div>
          <p className="text-sm text-text-secondary mt-1">
            {t("umapSubtitle")}
          </p>
          {data && data.points.length > 0 && (
            <div className="flex items-center gap-3 text-sm text-text-secondary mt-1">
              <span>
                {data.total_proteins} {t("proteins")}
              </span>
              {data.is_precomputed && (
                <span className="text-xs text-text-muted">(cached)</span>
              )}
            </div>
          )}
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

      {/* Content */}
      {renderContent()}
    </div>
  );
}
