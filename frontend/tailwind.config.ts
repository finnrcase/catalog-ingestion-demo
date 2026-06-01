import type { Config } from "tailwindcss";

const config: Config = {
  content: ["./app/**/*.{ts,tsx}", "./components/**/*.{ts,tsx}", "./lib/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        ivory: "#FAFAF8",
        paper: "#FFFFFF",
        charcoal: "#1F2933",
        bronze: "rgb(var(--accent-rgb) / <alpha-value>)",
        taupe: "#6B7280",
        linen: "#E5E7EB",
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
