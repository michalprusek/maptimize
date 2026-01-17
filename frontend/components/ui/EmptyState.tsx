"use client";

import { motion } from "framer-motion";
import { LucideIcon } from "lucide-react";
import {
  emptyStateContainerVariants,
  emptyStateItemVariants,
  floatVariants,
  buttonHoverProps,
} from "@/lib/animations";

interface EmptyStateProps {
  icon: LucideIcon;
  title: string;
  description: string;
  action?: {
    label: string;
    onClick: () => void;
    icon?: LucideIcon;
  };
}

/**
 * Empty state component with staggered entrance animation.
 * Elements animate in sequence: icon → title → description → button
 */
export function EmptyState({
  icon: Icon,
  title,
  description,
  action,
}: EmptyStateProps): JSX.Element {
  return (
    <motion.div
      className="glass-card p-12 text-center"
      variants={emptyStateContainerVariants}
      initial="hidden"
      animate="visible"
    >
      {/* Floating icon */}
      <motion.div
        className="w-16 h-16 bg-primary-500/10 rounded-2xl flex items-center justify-center mx-auto mb-4"
        variants={emptyStateItemVariants}
      >
        <motion.div
          variants={floatVariants}
          initial="initial"
          animate="animate"
        >
          <Icon className="w-8 h-8 text-primary-400" />
        </motion.div>
      </motion.div>

      {/* Title */}
      <motion.h3
        className="text-lg font-display font-semibold text-text-primary mb-2"
        variants={emptyStateItemVariants}
      >
        {title}
      </motion.h3>

      {/* Description */}
      <motion.p
        className="text-text-secondary mb-6 max-w-md mx-auto"
        variants={emptyStateItemVariants}
      >
        {description}
      </motion.p>

      {/* Action button */}
      {action && (
        <motion.button
          onClick={action.onClick}
          className="btn-primary inline-flex items-center gap-2"
          variants={emptyStateItemVariants}
          {...buttonHoverProps}
        >
          {action.icon && <action.icon className="w-5 h-5" />}
          {action.label}
        </motion.button>
      )}
    </motion.div>
  );
}
