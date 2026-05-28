import type { Config } from "tailwindcss";

const config: Config = {
  content: ["./app/**/*.{ts,tsx}", "./components/**/*.{ts,tsx}", "./lib/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        ivory: "rgb(var(--color-ivory) / <alpha-value>)",
        paper: "rgb(var(--color-paper) / <alpha-value>)",
        charcoal: "rgb(var(--color-charcoal) / <alpha-value>)",
        bronze: "rgb(var(--color-bronze) / <alpha-value>)",
        taupe: "rgb(var(--color-taupe) / <alpha-value>)",
        linen: "rgb(var(--color-linen) / <alpha-value>)",
        sage: "rgb(var(--color-sage) / <alpha-value>)",
        clay: "rgb(var(--color-clay) / <alpha-value>)",
        orange: "rgb(var(--color-bronze) / <alpha-value>)",
        orangeSoft: "rgb(var(--color-orange-soft) / <alpha-value>)",
        orangeHover: "rgb(var(--color-orange-hover) / <alpha-value>)",
        orangeBorder: "rgb(var(--color-orange-border) / <alpha-value>)",
      },
      boxShadow: {
        panel: "0 18px 50px rgb(var(--color-shadow) / 0.18)",
      },
    },
  },
  plugins: [],
};

export default config;
