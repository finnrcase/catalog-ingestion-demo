import type { Config } from "tailwindcss";

const config: Config = {
  content: ["./app/**/*.{ts,tsx}", "./components/**/*.{ts,tsx}", "./lib/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        ivory: "rgb(var(--app-bg-rgb) / <alpha-value>)",
        paper: "rgb(var(--panel-rgb) / <alpha-value>)",
        white: "rgb(var(--panel-rgb) / <alpha-value>)",
        charcoal: "rgb(var(--text-rgb) / <alpha-value>)",
        bronze: "rgb(var(--accent-rgb) / <alpha-value>)",
        taupe: "rgb(var(--muted-rgb) / <alpha-value>)",
        linen: "rgb(var(--border-rgb) / <alpha-value>)",
        sage: "#5F7A65",
        clay: "#A6533A",
        orangeSoft: "rgb(var(--accent-soft-rgb) / <alpha-value>)",
        orangeHover: "rgb(var(--accent-hover-rgb) / <alpha-value>)",
        orangeBorder: "rgb(var(--accent-ring-rgb) / <alpha-value>)",
      },
      boxShadow: {
        panel: "0 18px 50px rgba(31, 41, 51, 0.07)",
      },
    },
  },
  plugins: [],
};

export default config;
