"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { motion } from "framer-motion";
import { useQuery } from "@tanstack/react-query";
import { api, Experiment } from "@/lib/api";
import { useAuthStore } from "@/stores/authStore";
import {
  FolderOpen,
  Image as ImageIcon,
  Plus,
  ArrowRight,
  Sparkles,
} from "lucide-react";

const containerVariants = {
  hidden: { opacity: 0 },
  show: {
    opacity: 1,
    transition: {
      staggerChildren: 0.1,
    },
  },
};

const itemVariants = {
  hidden: { opacity: 0, y: 20 },
  show: { opacity: 1, y: 0 },
};

export default function DashboardPage() {
  const { user } = useAuthStore();

  const { data: experiments, isLoading } = useQuery({
    queryKey: ["experiments"],
    queryFn: () => api.getExperiments(),
  });

  // Calculate stats
  const stats = {
    experiments: experiments?.length || 0,
    images: experiments?.reduce((acc, exp) => acc + exp.image_count, 0) || 0,
  };

  return (
    <motion.div
      variants={containerVariants}
      initial="hidden"
      animate="show"
      className="space-y-8"
    >
      {/* Header */}
      <motion.div variants={itemVariants}>
        <h1 className="text-3xl font-display font-bold text-text-primary">
          Welcome back, {user?.name?.split(" ")[0]}
        </h1>
        <p className="text-text-secondary mt-2">
          Here's an overview of your microtubule analysis
        </p>
      </motion.div>

      {/* Stats */}
      <motion.div
        variants={itemVariants}
        className="grid grid-cols-1 md:grid-cols-2 gap-6"
      >
        {[
          {
            label: "Experiments",
            value: stats.experiments,
            icon: FolderOpen,
            color: "primary",
          },
          {
            label: "Images",
            value: stats.images,
            icon: ImageIcon,
            color: "amber",
          },
        ].map((stat) => (
          <div
            key={stat.label}
            className="glass-card p-6 flex items-center gap-4"
          >
            <div
              className={`p-3 rounded-xl ${
                stat.color === "primary"
                  ? "bg-primary-500/20 text-primary-400"
                  : "bg-accent-amber/20 text-accent-amber"
              }`}
            >
              <stat.icon className="w-6 h-6" />
            </div>
            <div>
              <p className="text-2xl font-display font-bold text-text-primary">
                {stat.value}
              </p>
              <p className="text-sm text-text-secondary">{stat.label}</p>
            </div>
          </div>
        ))}
      </motion.div>

      {/* Recent Experiments */}
      <motion.div variants={itemVariants}>
        <div className="flex items-center justify-between mb-4">
          <h2 className="text-xl font-display font-semibold text-text-primary">
            Recent Experiments
          </h2>
          <Link
            href="/dashboard/experiments"
            className="text-primary-400 hover:text-primary-300 text-sm font-medium flex items-center gap-1"
          >
            View all
            <ArrowRight className="w-4 h-4" />
          </Link>
        </div>

        {isLoading ? (
          <div className="glass-card p-8 flex justify-center">
            <div className="w-8 h-8 border-2 border-primary-500 border-t-transparent rounded-full animate-spin" />
          </div>
        ) : experiments && experiments.length > 0 ? (
          <div className="space-y-3">
            {experiments.slice(0, 5).map((exp) => (
              <Link key={exp.id} href={`/dashboard/experiments/${exp.id}`}>
                <motion.div
                  whileHover={{ x: 4 }}
                  className="glass-card p-4 flex items-center justify-between cursor-pointer hover:border-primary-500/30 transition-colors"
                >
                  <div className="flex items-center gap-4">
                    <div className="p-2 bg-primary-500/10 rounded-lg">
                      <FolderOpen className="w-5 h-5 text-primary-400" />
                    </div>
                    <div>
                      <h3 className="font-medium text-text-primary">
                        {exp.name}
                      </h3>
                      <p className="text-sm text-text-secondary">
                        {exp.image_count} images
                      </p>
                    </div>
                  </div>
                  <div className="flex items-center gap-4">
                    <span
                      className={`px-2 py-1 rounded-full text-xs font-medium ${
                        exp.status === "active"
                          ? "bg-primary-500/20 text-primary-400"
                          : exp.status === "completed"
                          ? "bg-accent-cyan/20 text-accent-cyan"
                          : "bg-text-muted/20 text-text-muted"
                      }`}
                    >
                      {exp.status}
                    </span>
                    <ArrowRight className="w-5 h-5 text-text-muted" />
                  </div>
                </motion.div>
              </Link>
            ))}
          </div>
        ) : (
          <div className="glass-card p-12 text-center">
            <div className="w-16 h-16 bg-primary-500/10 rounded-2xl flex items-center justify-center mx-auto mb-4">
              <Sparkles className="w-8 h-8 text-primary-400" />
            </div>
            <h3 className="text-lg font-display font-semibold text-text-primary mb-2">
              No experiments yet
            </h3>
            <p className="text-text-secondary mb-6">
              Create your first experiment to start analyzing microtubules
            </p>
            <Link href="/dashboard/experiments">
              <button className="btn-primary inline-flex items-center gap-2">
                <Plus className="w-5 h-5" />
                Create Experiment
              </button>
            </Link>
          </div>
        )}
      </motion.div>
    </motion.div>
  );
}
