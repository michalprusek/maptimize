"use client";

import { clsx } from "clsx";

interface LogoProps {
  className?: string;
  size?: "sm" | "md" | "lg" | "xl";
  showText?: boolean;
  animated?: boolean;
  transparent?: boolean;
}

const sizeMap = {
  sm: "w-6 h-6",
  md: "w-8 h-8",
  lg: "w-12 h-12",
  xl: "w-16 h-16",
};

const textSizeMap = {
  sm: "text-lg",
  md: "text-xl",
  lg: "text-2xl",
  xl: "text-3xl",
};

export function Logo({
  className,
  size = "md",
  showText = false,
  animated = false,
  transparent = false,
}: LogoProps) {
  const svgElement = (
    <svg
      viewBox="0 0 64 64"
      fill="none"
      xmlns="http://www.w3.org/2000/svg"
      className={clsx(
        sizeMap[size],
        animated && "animate-glow-pulse",
        !showText && className
      )}
    >
        <defs>
          {/* Glow filter for fluorescence effect */}
          <filter id="logoGlow" x="-50%" y="-50%" width="200%" height="200%">
            <feGaussianBlur stdDeviation="2" result="coloredBlur" />
            <feMerge>
              <feMergeNode in="coloredBlur" />
              <feMergeNode in="SourceGraphic" />
            </feMerge>
          </filter>

          {/* Stronger glow for accent elements */}
          <filter id="logoGlowStrong" x="-50%" y="-50%" width="200%" height="200%">
            <feGaussianBlur stdDeviation="3" result="coloredBlur" />
            <feMerge>
              <feMergeNode in="coloredBlur" />
              <feMergeNode in="coloredBlur" />
              <feMergeNode in="SourceGraphic" />
            </feMerge>
          </filter>

          {/* Gradient for microtubule strands */}
          <linearGradient id="logoTubulinGradient" x1="0%" y1="0%" x2="0%" y2="100%">
            <stop offset="0%" stopColor="currentColor" stopOpacity="0.3" />
            <stop offset="50%" stopColor="currentColor" stopOpacity="1" />
            <stop offset="100%" stopColor="currentColor" stopOpacity="0.3" />
          </linearGradient>
        </defs>

        {/* Background circle (subtle) */}
        {!transparent && (
          <circle cx="32" cy="32" r="30" fill="currentColor" fillOpacity="0.05" />
        )}

        {/* Microtubule strands forming "M" shape */}
        <g filter="url(#logoGlow)" className="text-primary-500">
          {/* Left strand */}
          <path
            d="M14 52 L14 16 L22 32"
            stroke="url(#logoTubulinGradient)"
            strokeWidth="4"
            strokeLinecap="round"
            strokeLinejoin="round"
            fill="none"
          />

          {/* Middle strand (V shape) */}
          <path
            d="M22 32 L32 48 L42 32"
            stroke="currentColor"
            strokeWidth="4"
            strokeLinecap="round"
            strokeLinejoin="round"
            fill="none"
          />

          {/* Right strand */}
          <path
            d="M42 32 L50 16 L50 52"
            stroke="url(#logoTubulinGradient)"
            strokeWidth="4"
            strokeLinecap="round"
            strokeLinejoin="round"
            fill="none"
          />
        </g>

        {/* MAP proteins (glowing dots on the microtubules) */}
        <g filter="url(#logoGlowStrong)">
          {/* Left strand MAPs */}
          <circle cx="14" cy="24" r="3" className="fill-accent-pink" />
          <circle cx="14" cy="44" r="2.5" className="fill-primary-500" />

          {/* Middle MAP */}
          <circle cx="32" cy="48" r="3.5" className="fill-primary-400" />

          {/* Right strand MAPs */}
          <circle cx="50" cy="28" r="2.5" className="fill-accent-pink" />
          <circle cx="50" cy="40" r="3" className="fill-primary-500" />
        </g>

        {/* Subtle connecting lines (like protein binding) */}
        <g
          stroke="currentColor"
          strokeOpacity="0.3"
          strokeWidth="1"
          strokeDasharray="2,2"
        >
          <line x1="14" y1="24" x2="22" y2="30" />
          <line x1="50" y1="28" x2="42" y2="32" />
        </g>
      </svg>
  );

  if (!showText) {
    return svgElement;
  }

  return (
    <div className={clsx("flex items-center gap-3", className)}>
      {svgElement}
      <span
        className={clsx(
          "font-display font-bold text-gradient",
          textSizeMap[size]
        )}
      >
        MAPtimize
      </span>
    </div>
  );
}

export default Logo;
