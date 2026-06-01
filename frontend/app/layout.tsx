import type { Metadata } from "next";
import type { ReactNode } from "react";
import "./globals.css";

export const metadata: Metadata = {
  title: "SCH DesignOps Intake",
  description: "Internal intake tool for Saffron Case Homes.",
  icons: {
    icon: "/icon.svg",
    shortcut: "/icon.svg",
    apple: "/icon.svg",
  },
};

const themeInitScript = `
(function () {
  try {
    var key = "sch-intake-theme";
    var stored = window.localStorage.getItem(key);
    var preference = stored === "system" || stored === "light" || stored === "dark" ? stored : "dark";
    var resolved = preference === "system"
      ? (window.matchMedia && window.matchMedia("(prefers-color-scheme: light)").matches ? "light" : "dark")
      : preference;
    document.documentElement.dataset.themePreference = preference;
    document.documentElement.dataset.theme = resolved;
  } catch (error) {
    document.documentElement.dataset.themePreference = "dark";
    document.documentElement.dataset.theme = "dark";
  }
})();
`;

export default function RootLayout({ children }: { children: ReactNode }) {
  return (
    <html lang="en" data-theme="dark" data-theme-preference="dark" suppressHydrationWarning>
      <head>
        <script dangerouslySetInnerHTML={{ __html: themeInitScript }} />
      </head>
      <body>{children}</body>
    </html>
  );
}
