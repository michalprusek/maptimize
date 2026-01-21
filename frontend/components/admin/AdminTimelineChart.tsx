"use client";

import {
  AreaChart,
  Area,
  XAxis,
  YAxis,
  ResponsiveContainer,
  Tooltip,
  CartesianGrid,
  Legend,
} from "recharts";
import type { AdminTimelinePoint } from "@/lib/api";

interface AdminTimelineChartProps {
  data: AdminTimelinePoint[];
  height?: number;
}

function formatDate(dateStr: string): string {
  const date = new Date(dateStr);
  return date.toLocaleDateString("en-US", { month: "short", day: "numeric" });
}

interface CustomTooltipProps {
  active?: boolean;
  payload?: Array<{ value: number; dataKey: string; color: string }>;
  label?: string;
}

function CustomTooltip({ active, payload, label }: CustomTooltipProps) {
  if (!active || !payload || !payload.length) return null;

  return (
    <div className="bg-bg-elevated border border-white/10 rounded-lg shadow-xl p-3">
      <p className="text-text-secondary text-sm mb-2">{label && formatDate(label)}</p>
      {payload.map((entry, index) => (
        <div key={index} className="flex items-center gap-2 text-sm">
          <span
            className="w-2.5 h-2.5 rounded-full"
            style={{ backgroundColor: entry.color }}
          />
          <span className="text-text-primary">
            {entry.dataKey === "registrations" ? "Registrations" : "Active Users"}: {entry.value}
          </span>
        </div>
      ))}
    </div>
  );
}

export function AdminTimelineChart({ data, height = 300 }: AdminTimelineChartProps) {
  const formattedData = data.map((point) => ({
    ...point,
    dateFormatted: formatDate(point.date),
  }));

  return (
    <ResponsiveContainer width="100%" height={height}>
      <AreaChart data={formattedData} margin={{ top: 10, right: 10, left: 0, bottom: 0 }}>
        <defs>
          <linearGradient id="colorRegistrations" x1="0" y1="0" x2="0" y2="1">
            <stop offset="5%" stopColor="#3b82f6" stopOpacity={0.3} />
            <stop offset="95%" stopColor="#3b82f6" stopOpacity={0} />
          </linearGradient>
          <linearGradient id="colorActive" x1="0" y1="0" x2="0" y2="1">
            <stop offset="5%" stopColor="#22c55e" stopOpacity={0.3} />
            <stop offset="95%" stopColor="#22c55e" stopOpacity={0} />
          </linearGradient>
        </defs>
        <CartesianGrid strokeDasharray="3 3" stroke="rgba(255,255,255,0.05)" />
        <XAxis
          dataKey="dateFormatted"
          tick={{ fill: "#6b7280", fontSize: 11 }}
          axisLine={{ stroke: "rgba(255,255,255,0.1)" }}
          tickLine={{ stroke: "rgba(255,255,255,0.1)" }}
          interval="preserveStartEnd"
          tickCount={7}
        />
        <YAxis
          tick={{ fill: "#6b7280", fontSize: 11 }}
          axisLine={{ stroke: "rgba(255,255,255,0.1)" }}
          tickLine={{ stroke: "rgba(255,255,255,0.1)" }}
          allowDecimals={false}
        />
        <Tooltip content={<CustomTooltip />} />
        <Legend
          wrapperStyle={{ paddingTop: 10 }}
          formatter={(value) => (
            <span className="text-text-secondary text-sm">
              {value === "registrations" ? "Registrations" : "Active Users"}
            </span>
          )}
        />
        <Area
          type="monotone"
          dataKey="registrations"
          stroke="#3b82f6"
          strokeWidth={2}
          fill="url(#colorRegistrations)"
          dot={false}
          activeDot={{ r: 4, fill: "#3b82f6" }}
        />
        <Area
          type="monotone"
          dataKey="active_users"
          stroke="#22c55e"
          strokeWidth={2}
          fill="url(#colorActive)"
          dot={false}
          activeDot={{ r: 4, fill: "#22c55e" }}
        />
      </AreaChart>
    </ResponsiveContainer>
  );
}
