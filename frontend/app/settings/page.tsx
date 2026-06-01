"use client";

import Link from "next/link";
import { useEffect, useState } from "react";

const DEBUG_MODE_STORAGE_KEY = "sch-intake-debug-mode";
const ACCENT_THEME_STORAGE_KEY = "sch-intake-accent-theme";

const accentThemes = [
  { id: "orange", label: "Orange", accent: "#f97316", soft: "#2c2118", ring: "#fdba74", foreground: "#ffffff", text: "#fb923c" },
  { id: "sage", label: "Sage", accent: "#5f7a65", soft: "#1f2a22", ring: "#a8b9ad", foreground: "#ffffff", text: "#a8b9ad" },
  { id: "blue", label: "Blue", accent: "#3f6f8f", soft: "#1b2730", ring: "#9ebbd0", foreground: "#ffffff", text: "#9ebbd0" },
  { id: "plum", label: "Plum", accent: "#7d5266", soft: "#2a2026", ring: "#c9aabb", foreground: "#ffffff", text: "#c9aabb" },
  { id: "mustard", label: "Mustard", accent: "#a87a22", soft: "#2b2517", ring: "#d9bd72", foreground: "#ffffff", text: "#d9bd72" },
  { id: "terracotta", label: "Terracotta", accent: "#a65f43", soft: "#2d211c", ring: "#d2a08c", foreground: "#ffffff", text: "#d2a08c" },
  { id: "slateBlue", label: "Slate Blue", accent: "#56657f", soft: "#202632", ring: "#a8b1c0", foreground: "#ffffff", text: "#a8b1c0" },
  { id: "sand", label: "Sand", accent: "#967f5c", soft: "#29251d", ring: "#cab892", foreground: "#ffffff", text: "#cab892" },
  { id: "forest", label: "Forest", accent: "#3f604b", soft: "#1c2920", ring: "#94ad9d", foreground: "#ffffff", text: "#94ad9d" },
  { id: "ocean", label: "Ocean", accent: "#2f7780", soft: "#192b2e", ring: "#91c0c5", foreground: "#ffffff", text: "#91c0c5" },
  { id: "clay", label: "Clay", accent: "#9a5a48", soft: "#2c211e", ring: "#c89b8e", foreground: "#ffffff", text: "#c89b8e" },
  { id: "rosewood", label: "Rosewood", accent: "#854f55", soft: "#2a2022", ring: "#c79da3", foreground: "#ffffff", text: "#c79da3" },
] as const;

type AccentThemeId = (typeof accentThemes)[number]["id"];

const fallbackBuildInfo = {
  commit:
    (process.env.NEXT_PUBLIC_APP_COMMIT_SHA || process.env.NEXT_PUBLIC_VERCEL_GIT_COMMIT_SHA)
      ?.slice(0, 12) || "local",
  apiBase: process.env.NEXT_PUBLIC_API_BASE_URL || "not configured",
};

function hexToRgbTriplet(hex: string) {
  const clean = hex.replace("#", "");
  const value = Number.parseInt(clean, 16);
  return `${(value >> 16) & 255} ${(value >> 8) & 255} ${value & 255}`;
}

function applyAccent(themeId: AccentThemeId) {
  const theme = accentThemes.find((item) => item.id === themeId) || accentThemes[0];
  const root = document.documentElement;
  root.style.setProperty("--accent", theme.accent);
  root.style.setProperty("--accent-rgb", hexToRgbTriplet(theme.accent));
  root.style.setProperty("--accent-hover", theme.accent);
  root.style.setProperty("--accent-hover-rgb", hexToRgbTriplet(theme.accent));
  root.style.setProperty("--accent-soft", theme.soft);
  root.style.setProperty("--accent-soft-rgb", hexToRgbTriplet(theme.soft));
  root.style.setProperty("--accent-ring", theme.ring);
  root.style.setProperty("--accent-ring-rgb", hexToRgbTriplet(theme.ring));
  root.style.setProperty("--accent-foreground", theme.foreground);
  root.style.setProperty("--accent-text", theme.text);
}

export default function SettingsPage() {
  const [debugMode, setDebugMode] = useState(false);
  const [accentThemeId, setAccentThemeId] = useState<AccentThemeId>("orange");
  const [apiStatus, setApiStatus] = useState("checking");

  useEffect(() => {
    const storedDebug = window.localStorage.getItem(DEBUG_MODE_STORAGE_KEY) === "true";
    const storedAccent = window.localStorage.getItem(ACCENT_THEME_STORAGE_KEY) as AccentThemeId | null;
    setDebugMode(storedDebug);
    if (storedAccent && accentThemes.some((theme) => theme.id === storedAccent)) {
      setAccentThemeId(storedAccent);
      applyAccent(storedAccent);
    } else {
      applyAccent("orange");
    }
  }, []);

  useEffect(() => {
    window.localStorage.setItem(DEBUG_MODE_STORAGE_KEY, String(debugMode));
  }, [debugMode]);

  useEffect(() => {
    window.localStorage.setItem(ACCENT_THEME_STORAGE_KEY, accentThemeId);
    applyAccent(accentThemeId);
  }, [accentThemeId]);

  useEffect(() => {
    const apiBase = fallbackBuildInfo.apiBase;
    if (!apiBase || apiBase === "not configured") {
      setApiStatus("misconfigured");
      return;
    }
    fetch(`${apiBase.replace(/\/$/, "")}/health`, { cache: "no-store" })
      .then((response) => setApiStatus(response.ok ? "connected" : "offline"))
      .catch(() => setApiStatus("offline"));
  }, []);

  return (
    <main className="min-h-screen px-4 py-6 sm:px-7 lg:px-10">
      <div className="mx-auto max-w-4xl rounded-[28px] border border-linen bg-white/70 p-6 shadow-panel">
        <div className="flex flex-col gap-4 border-b border-linen pb-5 sm:flex-row sm:items-center sm:justify-between">
          <div>
            <p className="text-xs font-semibold uppercase tracking-[0.22em] text-bronze">SCH FRONTEND v2 ACTIVE</p>
            <h1 className="mt-2 text-3xl font-semibold text-charcoal">Production frontend settings</h1>
            <p className="mt-2 text-sm leading-6 text-taupe">
              Color themes, debug mode, backend status, and export preferences for the live Next frontend.
            </p>
          </div>
          <Link href="/" className="btn-primary inline-flex h-11 items-center justify-center rounded-xl px-5 text-sm font-semibold">
            Open Intake
          </Link>
        </div>

        <section className="mt-5 rounded-2xl border border-linen bg-ivory/70 p-4">
          <h2 className="text-sm font-semibold text-charcoal">Appearance</h2>
          <p className="mt-1 text-sm text-taupe">Choose the accent used for buttons, workflow stages, highlights, and selected controls.</p>
          <div className="mt-4 grid grid-cols-2 gap-2 sm:grid-cols-3 md:grid-cols-4">
            {accentThemes.map((theme) => {
              const selected = accentThemeId === theme.id;
              return (
                <button
                  key={theme.id}
                  type="button"
                  className={`flex items-center gap-2 rounded-xl border px-3 py-2 text-left text-sm font-semibold transition ${
                    selected ? "border-orangeBorder bg-orangeSoft text-bronze" : "border-linen bg-white/60 text-charcoal hover:border-orangeBorder"
                  }`}
                  onClick={() => setAccentThemeId(theme.id)}
                >
                  <span className="h-4 w-4 rounded-full border border-white/30" style={{ backgroundColor: theme.accent }} />
                  {theme.label}
                </button>
              );
            })}
          </div>
        </section>

        <section className="mt-4 rounded-2xl border border-linen bg-white/58 p-4">
          <h2 className="text-sm font-semibold text-charcoal">Debug & Diagnostics</h2>
          <label className="mt-3 flex items-start gap-3 text-sm text-taupe">
            <input
              type="checkbox"
              checked={debugMode}
              onChange={(event) => setDebugMode(event.target.checked)}
              className="mt-1 h-4 w-4 accent-bronze"
            />
            <span>
              <span className="block font-semibold text-charcoal">Debug Mode</span>
              Default OFF. When enabled, the intake workspace shows safe route, endpoint, status, and sanitized error traces.
            </span>
          </label>
        </section>

        <section className="mt-4 rounded-2xl border border-linen bg-ivory/70 p-4">
          <h2 className="text-sm font-semibold text-charcoal">Backend / API Status</h2>
          <dl className="mt-3 grid gap-2 text-xs text-taupe sm:grid-cols-2">
            <div>
              <dt className="font-semibold uppercase tracking-[0.1em] text-charcoal/50">Status</dt>
              <dd className="mt-1 font-mono text-charcoal">{apiStatus}</dd>
            </div>
            <div>
              <dt className="font-semibold uppercase tracking-[0.1em] text-charcoal/50">NEXT_PUBLIC_API_BASE_URL</dt>
              <dd className="mt-1 break-all font-mono text-charcoal">{fallbackBuildInfo.apiBase}</dd>
            </div>
            <div>
              <dt className="font-semibold uppercase tracking-[0.1em] text-charcoal/50">Homepage route</dt>
              <dd className="mt-1 font-mono text-charcoal">frontend/app/page.tsx</dd>
            </div>
            <div>
              <dt className="font-semibold uppercase tracking-[0.1em] text-charcoal/50">Commit</dt>
              <dd className="mt-1 font-mono text-charcoal">{fallbackBuildInfo.commit}</dd>
            </div>
          </dl>
        </section>

        <section className="mt-4 rounded-2xl border border-linen bg-white/58 p-4">
          <h2 className="text-sm font-semibold text-charcoal">Export Preferences</h2>
          <dl className="mt-3 grid gap-2 text-xs text-taupe sm:grid-cols-2">
            <div>
              <dt className="font-semibold uppercase tracking-[0.1em] text-charcoal/50">Primary export</dt>
              <dd className="mt-1 text-charcoal">Download Excel for Programa (.xlsx)</dd>
            </div>
            <div>
              <dt className="font-semibold uppercase tracking-[0.1em] text-charcoal/50">Secondary exports</dt>
              <dd className="mt-1 text-charcoal">CSV, ZIP with Images, Debug Report</dd>
            </div>
          </dl>
        </section>
      </div>
    </main>
  );
}
