import type { Config } from "tailwindcss";

const config: Config = {
  darkMode: "class",
  content: [
    "./pages/**/*.{js,ts,jsx,tsx,mdx}",
    "./components/**/*.{js,ts,jsx,tsx,mdx}",
    "./app/**/*.{js,ts,jsx,tsx,mdx}",
  ],
  theme: {
    extend: {
      colors: {
        // Primary - Fluorescent teal (like GFP) - key shades use CSS vars for theming
        primary: {
          50: "#e6fff9",
          100: "#b3ffed",
          200: "#80ffe1",
          300: "#4dffd5",
          400: "var(--color-primary-400)",
          500: "var(--color-primary-500)",
          600: "var(--color-primary-600)",
          700: "#009c7d",
          800: "#008066",
          900: "#004d40",
          950: "#00332a",
        },
        // Background - Uses CSS variables for theme switching
        bg: {
          primary: "var(--color-bg-primary)",
          secondary: "var(--color-bg-secondary)",
          elevated: "var(--color-bg-elevated)",
          hover: "var(--color-bg-hover)",
        },
        // Accent colors for proteins
        accent: {
          pink: "#e91e8c",
          purple: "#9c27b0",
          amber: "#ffc107",
          cyan: "#00bcd4",
          red: "#ff6b6b",
        },
        // Text - Uses CSS variables for theme switching
        text: {
          primary: "var(--color-text-primary)",
          secondary: "var(--color-text-secondary)",
          muted: "var(--color-text-muted)",
        },
        // Border
        border: {
          DEFAULT: "var(--color-border)",
        },
      },
      fontFamily: {
        display: ["Outfit", "system-ui", "sans-serif"],
        body: ["IBM Plex Sans", "system-ui", "sans-serif"],
        mono: ["JetBrains Mono", "monospace"],
      },
      backgroundImage: {
        "gradient-radial": "radial-gradient(var(--tw-gradient-stops))",
        "cellular-pattern":
          "url(\"data:image/svg+xml,%3Csvg width='60' height='60' viewBox='0 0 60 60' xmlns='http://www.w3.org/2000/svg'%3E%3Cg fill='none' fill-rule='evenodd'%3E%3Cg fill='%2300d4aa' fill-opacity='0.03'%3E%3Cpath d='M36 34v-4h-2v4h-4v2h4v4h2v-4h4v-2h-4zm0-30V0h-2v4h-4v2h4v4h2V6h4V4h-4zM6 34v-4H4v4H0v2h4v4h2v-4h4v-2H6zM6 4V0H4v4H0v2h4v4h2V6h4V4H6z'/%3E%3C/g%3E%3C/g%3E%3C/svg%3E\")",
      },
      animation: {
        "glow-pulse": "glow-pulse 2s ease-in-out infinite",
        "float": "float 6s ease-in-out infinite",
        // New micro-interaction animations
        "pulse-soft": "pulse-soft 2s ease-in-out infinite",
        "shimmer": "shimmer 2s linear infinite",
        "shimmer-subtle": "shimmer-subtle 1.5s ease-in-out infinite",
        "check-bounce": "check-bounce 0.3s cubic-bezier(0.68, -0.55, 0.265, 1.55)",
        "slide-in-up": "slide-in-up 0.3s ease-out",
        "slide-in-left": "slide-in-left 0.3s ease-out",
        "slide-in-right": "slide-in-right 0.3s ease-out forwards",
        "fade-in": "fade-in 0.2s ease-out",
        "scale-in": "scale-in 0.2s ease-out",
        // Chat-specific animations
        "message-in": "message-in 0.3s ease-out forwards",
        "typing-dot": "typing-dot 1.4s ease-in-out infinite",
        "thinking-shimmer": "thinking-shimmer 2s linear infinite",
        "thinking-pulse": "thinking-pulse 2s ease-in-out infinite",
        "sparkle-rotate": "sparkle-rotate 3s linear infinite",
        "glow-ring": "glow-ring 0.2s ease-out forwards",
        "float-glow": "float-glow 6s ease-in-out infinite",
        "border-pulse": "border-pulse 1s ease-in-out infinite",
        "avatar-pulse": "avatar-pulse 0.6s ease-out",
      },
      keyframes: {
        "glow-pulse": {
          "0%, 100%": { boxShadow: "0 0 20px rgba(0, 212, 170, 0.3)" },
          "50%": { boxShadow: "0 0 40px rgba(0, 212, 170, 0.5)" },
        },
        float: {
          "0%, 100%": { transform: "translateY(0px)" },
          "50%": { transform: "translateY(-10px)" },
        },
        // Subtle pulse for status badges (processing states)
        "pulse-soft": {
          "0%, 100%": { opacity: "1", transform: "scale(1)" },
          "50%": { opacity: "0.8", transform: "scale(1.02)" },
        },
        // Loading skeleton shimmer effect
        "shimmer": {
          "0%": { backgroundPosition: "-200% 0" },
          "100%": { backgroundPosition: "200% 0" },
        },
        // Subtle shimmer for indexing progress
        "shimmer-subtle": {
          "0%": { backgroundPosition: "200% 0" },
          "100%": { backgroundPosition: "-200% 0" },
        },
        // Checkbox bounce animation
        "check-bounce": {
          "0%": { transform: "scale(0)", opacity: "0" },
          "50%": { transform: "scale(1.2)" },
          "100%": { transform: "scale(1)", opacity: "1" },
        },
        // Entrance animations
        "slide-in-up": {
          "0%": { transform: "translateY(10px)", opacity: "0" },
          "100%": { transform: "translateY(0)", opacity: "1" },
        },
        "slide-in-left": {
          "0%": { transform: "translateX(-10px)", opacity: "0" },
          "100%": { transform: "translateX(0)", opacity: "1" },
        },
        "slide-in-right": {
          "0%": { transform: "translateX(100%)", opacity: "0" },
          "100%": { transform: "translateX(0)", opacity: "1" },
        },
        "fade-in": {
          "0%": { opacity: "0" },
          "100%": { opacity: "1" },
        },
        "scale-in": {
          "0%": { transform: "scale(0.95)", opacity: "0" },
          "100%": { transform: "scale(1)", opacity: "1" },
        },
        // Chat message entrance (slide up + fade)
        "message-in": {
          "0%": { transform: "translateY(16px)", opacity: "0" },
          "100%": { transform: "translateY(0)", opacity: "1" },
        },
        // Typing indicator dot animation
        "typing-dot": {
          "0%, 60%, 100%": { transform: "translateY(0)", opacity: "0.4" },
          "30%": { transform: "translateY(-6px)", opacity: "1" },
        },
        // Thinking shimmer text effect
        "thinking-shimmer": {
          "0%": { backgroundPosition: "-200% center" },
          "100%": { backgroundPosition: "200% center" },
        },
        // Brain/sparkle pulse
        "thinking-pulse": {
          "0%, 100%": { transform: "scale(1)", opacity: "0.7" },
          "50%": { transform: "scale(1.15)", opacity: "1" },
        },
        // Rotating sparkle
        "sparkle-rotate": {
          "0%": { transform: "rotate(0deg)" },
          "100%": { transform: "rotate(360deg)" },
        },
        // Input focus glow ring
        "glow-ring": {
          "0%": { boxShadow: "0 0 0 0 rgba(0, 212, 170, 0)" },
          "100%": { boxShadow: "0 0 0 4px rgba(0, 212, 170, 0.15), 0 0 20px rgba(0, 212, 170, 0.1)" },
        },
        // Welcome icon float with glow
        "float-glow": {
          "0%, 100%": {
            transform: "translateY(0px)",
            boxShadow: "0 0 20px rgba(0, 212, 170, 0.2)"
          },
          "50%": {
            transform: "translateY(-8px)",
            boxShadow: "0 0 30px rgba(0, 212, 170, 0.35)"
          },
        },
        // Pulsing border for drag-over state
        "border-pulse": {
          "0%, 100%": { borderColor: "rgba(0, 212, 170, 0.5)" },
          "50%": { borderColor: "rgba(0, 212, 170, 0.8)" },
        },
        // Avatar pulse on new message
        "avatar-pulse": {
          "0%": { transform: "scale(1)" },
          "50%": { transform: "scale(1.1)" },
          "100%": { transform: "scale(1)" },
        },
      },
    },
  },
  plugins: [],
};

export default config;
