import type { Config } from "tailwindcss";

const config: Config = {
  content: [
    "./pages/**/*.{js,ts,jsx,tsx,mdx}",
    "./components/**/*.{js,ts,jsx,tsx,mdx}",
    "./app/**/*.{js,ts,jsx,tsx,mdx}",
  ],
  theme: {
    extend: {
      colors: {
        // Primary - Fluorescent teal (like GFP)
        primary: {
          50: "#e6fff9",
          100: "#b3ffed",
          200: "#80ffe1",
          300: "#4dffd5",
          400: "#1affc9",
          500: "#00d4aa",
          600: "#00b894",
          700: "#009c7d",
          800: "#008066",
          900: "#004d40",
          950: "#00332a",
        },
        // Background - Dark, like microscope background
        bg: {
          primary: "#0a0f14",
          secondary: "#121a22",
          elevated: "#1a242e",
          hover: "#222e3a",
        },
        // Accent colors for proteins
        accent: {
          pink: "#e91e8c",
          purple: "#9c27b0",
          amber: "#ffc107",
          cyan: "#00bcd4",
          red: "#ff6b6b",
        },
        // Text
        text: {
          primary: "#e8f0f5",
          secondary: "#8ba3b5",
          muted: "#5a7285",
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
      },
    },
  },
  plugins: [],
};

export default config;
