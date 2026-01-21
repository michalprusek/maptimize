"use client";

import { PieChart, Pie, Cell, ResponsiveContainer, Tooltip } from "recharts";
import { formatBytes } from "@/lib/utils";

interface StorageData {
  images_storage_bytes: number;
  documents_storage_bytes: number;
}

interface AdminStorageChartProps {
  data: StorageData;
  height?: number;
}

const COLORS = ["#3b82f6", "#a855f7"];

interface CustomTooltipProps {
  active?: boolean;
  payload?: Array<{ value: number; name: string; payload: { color: string } }>;
}

function CustomTooltip({ active, payload }: CustomTooltipProps) {
  if (!active || !payload || !payload.length) return null;

  const entry = payload[0];
  return (
    <div className="bg-bg-elevated border border-white/10 rounded-lg shadow-xl p-3">
      <div className="flex items-center gap-2">
        <span
          className="w-2.5 h-2.5 rounded-full"
          style={{ backgroundColor: entry.payload.color }}
        />
        <span className="text-text-primary text-sm">{entry.name}</span>
      </div>
      <p className="text-text-secondary text-sm mt-1">{formatBytes(entry.value)}</p>
    </div>
  );
}

export function AdminStorageChart({ data, height = 200 }: AdminStorageChartProps) {
  const chartData = [
    { name: "Images", value: data.images_storage_bytes, color: COLORS[0] },
    { name: "Documents", value: data.documents_storage_bytes, color: COLORS[1] },
  ].filter((d) => d.value > 0);

  const total = data.images_storage_bytes + data.documents_storage_bytes;

  if (total === 0) {
    return (
      <div className="flex items-center justify-center h-full text-text-muted">
        No storage data
      </div>
    );
  }

  return (
    <div className="flex items-center gap-4">
      <ResponsiveContainer width="60%" height={height}>
        <PieChart>
          <Pie
            data={chartData}
            cx="50%"
            cy="50%"
            innerRadius={50}
            outerRadius={70}
            paddingAngle={2}
            dataKey="value"
          >
            {chartData.map((entry, index) => (
              <Cell key={`cell-${index}`} fill={entry.color} />
            ))}
          </Pie>
          <Tooltip content={<CustomTooltip />} />
        </PieChart>
      </ResponsiveContainer>
      <div className="flex-1 space-y-3">
        <div className="text-center mb-4">
          <p className="text-2xl font-bold text-text-primary">{formatBytes(total)}</p>
          <p className="text-xs text-text-muted">Total Storage</p>
        </div>
        {chartData.map((item, index) => (
          <div key={index} className="flex items-center justify-between">
            <div className="flex items-center gap-2">
              <span
                className="w-2.5 h-2.5 rounded-full"
                style={{ backgroundColor: item.color }}
              />
              <span className="text-sm text-text-secondary">{item.name}</span>
            </div>
            <span className="text-sm text-text-primary">{formatBytes(item.value)}</span>
          </div>
        ))}
      </div>
    </div>
  );
}
