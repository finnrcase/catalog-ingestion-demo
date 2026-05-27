import type { Metadata } from "next";
import type { ReactNode } from "react";
import "./globals.css";

export const metadata: Metadata = {
  title: "SCH DesignOps Intake",
  description: "Internal intake tool for Saffron Case Homes.",
  icons: {
    icon: [
      { url: "/favicon.ico" },
      { url: "/favicon-32x32.png", sizes: "32x32", type: "image/png" },
    ],
    apple: [{ url: "/apple-touch-icon.png" }],
  },
};

export default function RootLayout({ children }: { children: ReactNode }) {
  return (
    <html lang="en" suppressHydrationWarning>
      <head>
        <script
          dangerouslySetInnerHTML={{
            __html: `
(() => {
  try {
    const theme = localStorage.getItem("sch:websiteTheme") || "light";
    const accent = localStorage.getItem("sch:accentColor") || "orange";
    const allowedThemes = new Set(["light", "dark", "graphite"]);
    const allowedAccents = new Set([
      "orange",
      "sage",
      "blue",
      "plum",
      "mustard",
      "terracotta",
      "slate-blue",
      "sand",
      "forest",
      "ocean",
      "clay",
      "rosewood",
    ]);
    document.documentElement.dataset.theme = allowedThemes.has(theme) ? theme : "light";
    document.documentElement.dataset.accent = allowedAccents.has(accent) ? accent : "orange";
  } catch {
    document.documentElement.dataset.theme = "light";
    document.documentElement.dataset.accent = "orange";
  }
})();
            `.trim(),
          }}
        />
      </head>
      <body>{children}</body>
    </html>
  );
}
