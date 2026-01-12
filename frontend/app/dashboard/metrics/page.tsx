"use client";

import { useState } from "react";
import { motion } from "framer-motion";
import { useQuery } from "@tanstack/react-query";
import { api } from "@/lib/api";
import {
  BarChart3,
  TrendingUp,
  Target,
  Zap,
  Info,
} from "lucide-react";

export default function MetricsPage() {
  const [selectedExperiment, setSelectedExperiment] = useState<number | null>(null);

  const { data: experiments } = useQuery({
    queryKey: ["experiments"],
    queryFn: () => api.getExperiments(),
  });

  const { data: leaderboard } = useQuery({
    queryKey: ["leaderboard", selectedExperiment],
    queryFn: () => api.getLeaderboard(selectedExperiment || undefined, 1, 50),
    enabled: true,
  });

  const { data: progress } = useQuery({
    queryKey: ["ranking-progress", selectedExperiment],
    queryFn: () => api.getRankingProgress(selectedExperiment || undefined),
  });

  // Calculate metrics
  const calculateMetrics = () => {
    if (!leaderboard?.items.length) return null;

    const bundlenessScores = leaderboard.items
      .filter((i) => i.bundleness_score !== null)
      .map((i) => i.bundleness_score!);

    const ordinalScores = leaderboard.items.map((i) => i.ordinal_score);

    return {
      avgBundleness: bundlenessScores.length
        ? (bundlenessScores.reduce((a, b) => a + b, 0) / bundlenessScores.length).toFixed(3)
        : "N/A",
      maxBundleness: bundlenessScores.length
        ? Math.max(...bundlenessScores).toFixed(3)
        : "N/A",
      avgOrdinal: (ordinalScores.reduce((a, b) => a + b, 0) / ordinalScores.length).toFixed(2),
      topRanked: leaderboard.items[0],
    };
  };

  const metrics = calculateMetrics();

  return (
    <div className="space-y-8">
      {/* Header */}
      <div>
        <h1 className="text-3xl font-display font-bold text-text-primary">
          Metrics
        </h1>
        <p className="text-text-secondary mt-2">
          Analyze bundleness scores and ranking statistics
        </p>
      </div>

      {/* Experiment selector */}
      <div className="glass-card p-6">
        <label className="block text-sm font-medium text-text-secondary mb-3">
          Filter by Experiment
        </label>
        <select
          value={selectedExperiment || ""}
          onChange={(e) => setSelectedExperiment(Number(e.target.value) || null)}
          className="input-field"
        >
          <option value="">All experiments</option>
          {experiments?.map((exp) => (
            <option key={exp.id} value={exp.id}>
              {exp.name}
            </option>
          ))}
        </select>
      </div>

      {/* Stats cards */}
      {metrics && (
        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-4">
          <motion.div
            initial={{ opacity: 0, y: 20 }}
            animate={{ opacity: 1, y: 0 }}
            className="glass-card p-6"
          >
            <div className="flex items-center gap-3 mb-3">
              <div className="p-2 bg-primary-500/20 rounded-lg">
                <BarChart3 className="w-5 h-5 text-primary-400" />
              </div>
              <span className="text-sm text-text-secondary">Avg Bundleness</span>
            </div>
            <p className="text-2xl font-display font-bold text-text-primary">
              {metrics.avgBundleness}
            </p>
          </motion.div>

          <motion.div
            initial={{ opacity: 0, y: 20 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ delay: 0.1 }}
            className="glass-card p-6"
          >
            <div className="flex items-center gap-3 mb-3">
              <div className="p-2 bg-accent-amber/20 rounded-lg">
                <Zap className="w-5 h-5 text-accent-amber" />
              </div>
              <span className="text-sm text-text-secondary">Max Bundleness</span>
            </div>
            <p className="text-2xl font-display font-bold text-text-primary">
              {metrics.maxBundleness}
            </p>
          </motion.div>

          <motion.div
            initial={{ opacity: 0, y: 20 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ delay: 0.2 }}
            className="glass-card p-6"
          >
            <div className="flex items-center gap-3 mb-3">
              <div className="p-2 bg-accent-cyan/20 rounded-lg">
                <TrendingUp className="w-5 h-5 text-accent-cyan" />
              </div>
              <span className="text-sm text-text-secondary">Avg Ordinal Score</span>
            </div>
            <p className="text-2xl font-display font-bold text-text-primary">
              {metrics.avgOrdinal}
            </p>
          </motion.div>

          <motion.div
            initial={{ opacity: 0, y: 20 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ delay: 0.3 }}
            className="glass-card p-6"
          >
            <div className="flex items-center gap-3 mb-3">
              <div className="p-2 bg-accent-pink/20 rounded-lg">
                <Target className="w-5 h-5 text-accent-pink" />
              </div>
              <span className="text-sm text-text-secondary">Total Cells</span>
            </div>
            <p className="text-2xl font-display font-bold text-text-primary">
              {leaderboard?.total || 0}
            </p>
          </motion.div>
        </div>
      )}

      {/* Bundleness explanation */}
      <div className="glass-card p-6">
        <div className="flex items-start gap-4">
          <div className="p-2 bg-primary-500/20 rounded-lg">
            <Info className="w-5 h-5 text-primary-400" />
          </div>
          <div>
            <h3 className="font-display font-semibold text-text-primary mb-2">
              About Bundleness Score
            </h3>
            <p className="text-text-secondary text-sm leading-relaxed">
              Bundleness is a PCA-combined metric from image intensity skewness and kurtosis.
              Higher values indicate more bundled microtubule structures. The formula is:
            </p>
            <code className="block mt-3 p-3 bg-bg-secondary rounded-lg text-sm font-mono text-primary-400">
              bundleness = 0.7071 × z_skewness + 0.7071 × z_kurtosis
            </code>
            <p className="text-text-muted text-xs mt-2">
              Parameters: mean_skewness=1.1327, std_skewness=0.4717, mean_kurtosis=1.0071, std_kurtosis=1.4920
            </p>
          </div>
        </div>
      </div>

      {/* Top ranked cells */}
      {leaderboard && leaderboard.items.length > 0 && (
        <div>
          <h2 className="text-xl font-display font-semibold text-text-primary mb-4">
            Top Ranked Cells
          </h2>
          <div className="glass-card overflow-hidden">
            <table className="w-full">
              <thead>
                <tr className="border-b border-white/5">
                  <th className="px-4 py-3 text-left text-sm font-medium text-text-secondary">
                    Rank
                  </th>
                  <th className="px-4 py-3 text-left text-sm font-medium text-text-secondary">
                    Cell ID
                  </th>
                  <th className="px-4 py-3 text-left text-sm font-medium text-text-secondary">
                    Protein
                  </th>
                  <th className="px-4 py-3 text-left text-sm font-medium text-text-secondary">
                    Ordinal Score
                  </th>
                  <th className="px-4 py-3 text-left text-sm font-medium text-text-secondary">
                    Bundleness
                  </th>
                  <th className="px-4 py-3 text-left text-sm font-medium text-text-secondary">
                    Comparisons
                  </th>
                </tr>
              </thead>
              <tbody>
                {leaderboard.items.slice(0, 20).map((item, i) => (
                  <motion.tr
                    key={item.cell_crop_id}
                    initial={{ opacity: 0 }}
                    animate={{ opacity: 1 }}
                    transition={{ delay: i * 0.03 }}
                    className="border-b border-white/5 last:border-0 hover:bg-white/5"
                  >
                    <td className="px-4 py-3">
                      <span className={`font-mono ${i < 3 ? 'text-primary-400 font-bold' : 'text-text-primary'}`}>
                        #{item.rank}
                      </span>
                    </td>
                    <td className="px-4 py-3 font-mono text-text-primary">
                      {item.cell_crop_id}
                    </td>
                    <td className="px-4 py-3">
                      {item.map_protein_name ? (
                        <span className="px-2 py-1 bg-primary-500/20 text-primary-400 rounded text-sm">
                          {item.map_protein_name}
                        </span>
                      ) : (
                        <span className="text-text-muted">-</span>
                      )}
                    </td>
                    <td className="px-4 py-3 font-mono text-text-primary">
                      {item.ordinal_score.toFixed(2)}
                    </td>
                    <td className="px-4 py-3 font-mono text-text-primary">
                      {item.bundleness_score?.toFixed(3) || "-"}
                    </td>
                    <td className="px-4 py-3 text-text-secondary">
                      {item.comparison_count}
                    </td>
                  </motion.tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}
    </div>
  );
}
