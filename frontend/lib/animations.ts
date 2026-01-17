/**
 * Reusable Animation Variants Library
 *
 * Centralized animation configurations for consistent motion design.
 * Based on Framer Motion best practices and Figma micro-interaction patterns.
 *
 * @see https://www.framer.com/motion/
 */
import type { Transition, Variants } from "framer-motion";

// =============================================================================
// SPRING CONFIGURATIONS
// =============================================================================

/**
 * Predefined spring physics configurations for different animation feels.
 * Higher stiffness = faster, higher damping = less bounce.
 */
export const springs = {
  /** Smooth, gentle transitions - good for subtle UI changes */
  gentle: { type: "spring", stiffness: 300, damping: 30 } as Transition,
  /** Responsive, snappy feel - good for buttons and interactive elements */
  snappy: { type: "spring", stiffness: 400, damping: 25 } as Transition,
  /** Playful bounce - good for success states and checkmarks */
  bouncy: { type: "spring", stiffness: 500, damping: 15 } as Transition,
  /** Quick, minimal bounce - good for layout transitions */
  stiff: { type: "spring", stiffness: 500, damping: 35 } as Transition,
} as const;

// =============================================================================
// STAGGER ANIMATIONS
// =============================================================================

/**
 * Container variants for staggered children animations.
 * Use with `variants={staggerContainerVariants}` and `initial="hidden" animate="visible"`.
 */
export const staggerContainerVariants: Variants = {
  hidden: { opacity: 0 },
  visible: {
    opacity: 1,
    transition: {
      staggerChildren: 0.03,
      delayChildren: 0.1,
    },
  },
};

/**
 * Slower stagger for nav items - more noticeable effect.
 */
export const navStaggerContainerVariants: Variants = {
  hidden: { opacity: 0 },
  visible: {
    opacity: 1,
    transition: {
      staggerChildren: 0.05,
      delayChildren: 0.15,
    },
  },
};

/**
 * Item variants for staggered grid/list animations.
 * Children of a container using staggerContainerVariants.
 */
export const staggerItemVariants: Variants = {
  hidden: { opacity: 0, y: 10, scale: 0.98 },
  visible: {
    opacity: 1,
    y: 0,
    scale: 1,
    transition: springs.gentle,
  },
};

/**
 * Navigation item variants with horizontal slide.
 */
export const navItemVariants: Variants = {
  hidden: { opacity: 0, x: -10 },
  visible: {
    opacity: 1,
    x: 0,
    transition: springs.gentle,
  },
};

// =============================================================================
// CHECK / TOGGLE ANIMATIONS
// =============================================================================

/**
 * Bouncy scale animation for checkmarks and success icons.
 */
export const checkIconVariants: Variants = {
  hidden: { scale: 0, opacity: 0 },
  visible: {
    scale: 1,
    opacity: 1,
    transition: springs.bouncy,
  },
};

/**
 * Ring glow animation for selection states.
 */
export const selectionRingVariants: Variants = {
  hidden: { scale: 0.8, opacity: 0 },
  visible: {
    scale: 1,
    opacity: 1,
    transition: springs.snappy,
  },
};

// =============================================================================
// EMPTY STATE / REVEAL ANIMATIONS
// =============================================================================

/**
 * Container for empty state staggered reveal.
 */
export const emptyStateContainerVariants: Variants = {
  hidden: { opacity: 0 },
  visible: {
    opacity: 1,
    transition: {
      staggerChildren: 0.12,
      delayChildren: 0.1,
    },
  },
};

/**
 * Item variants for empty state elements (icon, title, description, button).
 */
export const emptyStateItemVariants: Variants = {
  hidden: { opacity: 0, y: 20 },
  visible: {
    opacity: 1,
    y: 0,
    transition: springs.gentle,
  },
};

/**
 * Floating animation for empty state icons.
 */
export const floatVariants: Variants = {
  initial: { y: 0 },
  animate: {
    y: [-5, 5, -5],
    transition: {
      duration: 4,
      repeat: Infinity,
      ease: "easeInOut",
    },
  },
};

// =============================================================================
// CARD / GRID ITEM ANIMATIONS
// =============================================================================

/**
 * Exit animation for grid items being removed.
 */
export const gridItemExitVariants: Variants = {
  exit: {
    opacity: 0,
    scale: 0.9,
    transition: { duration: 0.2 },
  },
};

/**
 * Hover configuration for cards with lift effect.
 */
export const cardHoverProps = {
  whileHover: {
    scale: 1.02,
    boxShadow: "0 10px 40px -10px rgba(0, 212, 170, 0.2)",
  },
  whileTap: { scale: 0.98 },
  transition: springs.snappy,
};

/**
 * Hover configuration for buttons.
 */
export const buttonHoverProps = {
  whileHover: { scale: 1.02 },
  whileTap: { scale: 0.95 },
  transition: springs.snappy,
};

/**
 * Hover configuration for icon buttons (like delete).
 */
export const iconButtonHoverProps = {
  whileHover: { scale: 1.1 },
  whileTap: { scale: 0.9 },
  transition: springs.snappy,
};

// =============================================================================
// STATUS / BADGE ANIMATIONS
// =============================================================================

/**
 * Pulse animation for processing/active status indicators.
 */
export const pulseVariants: Variants = {
  initial: { scale: 1, opacity: 1 },
  animate: {
    scale: [1, 1.05, 1],
    opacity: [1, 0.8, 1],
    transition: {
      duration: 2,
      repeat: Infinity,
      ease: "easeInOut",
    },
  },
};

/**
 * Dot indicator animation for active states.
 */
export const activeDotVariants: Variants = {
  initial: { scale: 0 },
  animate: {
    scale: 1,
    transition: springs.bouncy,
  },
};

// =============================================================================
// LAYOUT ANIMATIONS
// =============================================================================

/**
 * Shared layout transition for sliding indicators (pagination, nav).
 */
export const layoutTransition: Transition = {
  type: "spring",
  stiffness: 500,
  damping: 35,
};

// =============================================================================
// PAGE TRANSITIONS
// =============================================================================

/**
 * Page entrance animation.
 */
export const pageVariants: Variants = {
  initial: { opacity: 0, y: 20 },
  animate: {
    opacity: 1,
    y: 0,
    transition: springs.gentle,
  },
  exit: {
    opacity: 0,
    y: -20,
    transition: { duration: 0.2 },
  },
};
