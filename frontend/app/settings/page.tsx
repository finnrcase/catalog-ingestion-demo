"use client";

import Link from "next/link";
import { useEffect, useState } from "react";

const DEBUG_MODE_STORAGE_KEY = "sch-intake-debug-mode";
const THEME_STORAGE_KEY = "sch-intake-theme";
const ACCENT_THEME_STORAGE_KEY = "sch-intake-accent-theme";
const UI_MODE_STORAGE_KEY = "sch-intake-ui-mode";

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
type ThemePreference = "system" | "dark" | "light";
type UiMode = "explanation" | "simple";

const themeOptions: { id: ThemePreference; label: string; description: string }[] = [
  { id: "light", label: "Light", description: "Warm premium light workspace." },
  { id: "dark", label: "Dark", description: "Current SCH dark workspace." },
  { id: "system", label: "System", description: "Follow browser or OS preference." },
];

const fallbackBuildInfo = {
  commit:
    (process.env.NEXT_PUBLIC_APP_COMMIT_SHA || process.env.NEXT_PUBLIC_VERCEL_GIT_COMMIT_SHA)
      ?.slice(0, 12) || "local",
  apiBase: process.env.NEXT_PUBLIC_API_BASE_URL || "not configured",
  repo: process.env.NEXT_PUBLIC_APP_REPO || "catalog-ingestion-demo",
  branch: process.env.NEXT_PUBLIC_APP_BRANCH || "not exposed",
  builtAt: process.env.NEXT_PUBLIC_APP_BUILD_TIMESTAMP || "not exposed",
  environment: process.env.NEXT_PUBLIC_VERCEL_ENV || process.env.NEXT_PUBLIC_APP_ENV || "not exposed",
  version: process.env.NEXT_PUBLIC_APP_VERSION || "0.1.0",
  project: "frontend",
  rootDirectory: "frontend",
  homepageRoute: "frontend/app/page.tsx",
  settingsRoute: "frontend/app/settings/page.tsx",
  workflowComponent: "frontend/components/intake-workspace.tsx",
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

function resolveThemePreference(preference: ThemePreference) {
  if (preference !== "system") return preference;
  return window.matchMedia?.("(prefers-color-scheme: light)").matches ? "light" : "dark";
}

function applyThemePreference(preference: ThemePreference) {
  document.documentElement.dataset.themePreference = preference;
  document.documentElement.dataset.theme = resolveThemePreference(preference);
}

export default function SettingsPage() {
  const [debugMode, setDebugMode] = useState(false);
  const [themePreference, setThemePreference] = useState<ThemePreference>("dark");
  const [accentThemeId, setAccentThemeId] = useState<AccentThemeId>("orange");
  const [uiMode, setUiMode] = useState<UiMode>("explanation");
  const [apiStatus, setApiStatus] = useState("checking");
  const isSimpleMode = uiMode === "simple";

  useEffect(() => {
    const storedDebug = window.localStorage.getItem(DEBUG_MODE_STORAGE_KEY) === "true";
    const storedTheme = window.localStorage.getItem(THEME_STORAGE_KEY);
    const storedAccent = window.localStorage.getItem(ACCENT_THEME_STORAGE_KEY) as AccentThemeId | null;
    const storedUiMode = window.localStorage.getItem(UI_MODE_STORAGE_KEY);
    setDebugMode(storedDebug);
    const nextTheme = storedTheme === "system" || storedTheme === "light" || storedTheme === "dark" ? storedTheme : "dark";
    setThemePreference(nextTheme);
    applyThemePreference(nextTheme);
    setUiMode(storedUiMode === "simple" ? "simple" : "explanation");
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
    window.localStorage.setItem(THEME_STORAGE_KEY, themePreference);
    applyThemePreference(themePreference);
    if (themePreference !== "system") return;
    const media = window.matchMedia?.("(prefers-color-scheme: light)");
    if (!media) return;
    const handleChange = () => applyThemePreference("system");
    media.addEventListener?.("change", handleChange);
    return () => media.removeEventListener?.("change", handleChange);
  }, [themePreference]);

  useEffect(() => {
    window.localStorage.setItem(ACCENT_THEME_STORAGE_KEY, accentThemeId);
    applyAccent(accentThemeId);
  }, [accentThemeId]);

  useEffect(() => {
    window.localStorage.setItem(UI_MODE_STORAGE_KEY, uiMode);
  }, [uiMode]);

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
            <h1 className="mt-2 text-3xl font-semibold text-charcoal">Production frontend settings</h1>
            {!isSimpleMode ? (
              <p className="mt-2 text-sm leading-6 text-taupe">
                Theme, interface mode, debug mode, and export preferences for the SCH workspace.
              </p>
            ) : null}
            <p className="mt-2 text-xs font-semibold uppercase tracking-[0.12em] text-taupe">
              UI mode: <span className="text-bronze">{isSimpleMode ? "Simple" : "Explanation"}</span>
            </p>
          </div>
          <Link href="/" className="btn-primary inline-flex h-11 items-center justify-center rounded-xl px-5 text-sm font-semibold">
            Open Intake
          </Link>
        </div>

        <section className="mt-5 rounded-2xl border border-linen bg-ivory/70 p-4">
          <h2 className="text-sm font-semibold text-charcoal">Appearance</h2>
          {!isSimpleMode ? (
            <p className="mt-1 text-sm text-taupe">Choose the workspace theme and SCH accent styling.</p>
          ) : null}
          <div className="mt-4">
            <div className="text-xs font-semibold uppercase tracking-[0.12em] text-charcoal/55">Theme</div>
            <div className="mt-2 grid gap-2 sm:grid-cols-3">
              {themeOptions.map((theme) => {
                const selected = themePreference === theme.id;
                return (
                  <button
                    key={theme.id}
                    type="button"
                    className={`rounded-xl border px-3 py-2 text-left text-sm font-semibold transition ${
                      selected ? "border-orangeBorder bg-orangeSoft text-bronze" : "border-linen bg-white/60 text-charcoal hover:border-orangeBorder"
                    }`}
                    onClick={() => setThemePreference(theme.id)}
                    aria-pressed={selected}
                  >
                    {theme.label}
                    {!isSimpleMode ? <span className="mt-1 block text-xs font-medium text-taupe">{theme.description}</span> : null}
                  </button>
                );
              })}
            </div>
          </div>
          <div className="mt-4">
            <div className="text-xs font-semibold uppercase tracking-[0.12em] text-charcoal/55">Accent</div>
            <div className="mt-2 grid grid-cols-2 gap-2 sm:grid-cols-3 md:grid-cols-4">
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
          </div>
        </section>

        <section className="mt-4 rounded-2xl border border-linen bg-white/58 p-4">
          <h2 className="text-sm font-semibold text-charcoal">Interface</h2>
          {!isSimpleMode ? (
            <p className="mt-1 text-sm text-taupe">Switch between a cleaner workflow and the fuller guided experience.</p>
          ) : null}
          <div className="mt-4 grid gap-2 sm:grid-cols-2">
            {(["simple", "explanation"] as UiMode[]).map((mode) => {
              const selected = uiMode === mode;
              return (
                <button
                  key={mode}
                  type="button"
                  className={`rounded-xl border px-3 py-2 text-left text-sm font-semibold transition ${
                    selected ? "border-orangeBorder bg-orangeSoft text-bronze" : "border-linen bg-white/60 text-charcoal hover:border-orangeBorder"
                  }`}
                  onClick={() => setUiMode(mode)}
                  aria-pressed={selected}
                >
                  {mode === "simple" ? "Simple Mode" : "Explanation Mode"}
                  {!isSimpleMode ? (
                    <span className="mt-1 block text-xs font-medium text-taupe">
                      {mode === "simple" ? "Minimal descriptions and cleaner cards." : "Helper text, onboarding descriptions, and extra context."}
                    </span>
                  ) : null}
                </button>
              );
            })}
          </div>
        </section>

        <section className="mt-4 rounded-2xl border border-linen bg-white/58 p-4">
          <h2 className="text-sm font-semibold text-charcoal">Developer</h2>
          <label className="mt-3 flex items-start gap-3 text-sm text-taupe">
            <input
              type="checkbox"
              checked={debugMode}
              onChange={(event) => setDebugMode(event.target.checked)}
              className="mt-1 h-4 w-4 accent-bronze"
            />
            <span>
              <span className="block font-semibold text-charcoal">Debug Mode</span>
              {!isSimpleMode
                ? "Default OFF. When enabled, the intake workspace shows safe route, endpoint, status, and sanitized error traces."
                : null}
              </span>
          </label>
          {debugMode ? (
            <details className="mt-4 rounded-xl border border-linen bg-white/70 p-3">
              <summary className="cursor-pointer text-xs font-semibold uppercase tracking-[0.12em] text-charcoal/60">
                Debug details
              </summary>
              <dl className="mt-3 grid gap-2 text-xs text-taupe sm:grid-cols-2">
                <div>
                  <dt className="font-semibold uppercase tracking-[0.1em] text-charcoal/50">Internal</dt>
                  <dd className="mt-1 font-mono text-charcoal">Enabled</dd>
                </div>
                <div>
                  <dt className="font-semibold uppercase tracking-[0.1em] text-charcoal/50">Repo</dt>
                  <dd className="mt-1 font-mono text-charcoal">{fallbackBuildInfo.repo}</dd>
                </div>
                <div>
                  <dt className="font-semibold uppercase tracking-[0.1em] text-charcoal/50">Branch</dt>
                  <dd className="mt-1 font-mono text-charcoal">{fallbackBuildInfo.branch}</dd>
                </div>
                <div>
                  <dt className="font-semibold uppercase tracking-[0.1em] text-charcoal/50">Live route</dt>
                  <dd className="mt-1 font-mono text-charcoal">{fallbackBuildInfo.homepageRoute}</dd>
                </div>
                <div>
                  <dt className="font-semibold uppercase tracking-[0.1em] text-charcoal/50">Build hash</dt>
                  <dd className="mt-1 font-mono text-charcoal">{fallbackBuildInfo.commit}</dd>
                </div>
                <div>
                  <dt className="font-semibold uppercase tracking-[0.1em] text-charcoal/50">Build timestamp</dt>
                  <dd className="mt-1 font-mono text-charcoal">{fallbackBuildInfo.builtAt}</dd>
                </div>
                <div>
                  <dt className="font-semibold uppercase tracking-[0.1em] text-charcoal/50">Backend status</dt>
                  <dd className="mt-1 font-mono text-charcoal">{apiStatus}</dd>
                </div>
                <div>
                  <dt className="font-semibold uppercase tracking-[0.1em] text-charcoal/50">Environment</dt>
                  <dd className="mt-1 font-mono text-charcoal">{fallbackBuildInfo.environment}</dd>
                </div>
                <div>
                  <dt className="font-semibold uppercase tracking-[0.1em] text-charcoal/50">Version</dt>
                  <dd className="mt-1 font-mono text-charcoal">v{fallbackBuildInfo.version}</dd>
                </div>
                <div>
                  <dt className="font-semibold uppercase tracking-[0.1em] text-charcoal/50">NEXT_PUBLIC_API_BASE_URL</dt>
                  <dd className="mt-1 break-all font-mono text-charcoal">{fallbackBuildInfo.apiBase}</dd>
                </div>
              </dl>
            </details>
          ) : null}
        </section>

        <section className="mt-4 rounded-2xl border border-linen bg-ivory/70 p-4">
          <h2 className="text-sm font-semibold text-charcoal">About</h2>
          <dl className="mt-3 grid gap-2 text-xs text-taupe sm:grid-cols-2">
            <div>
              <dt className="font-semibold uppercase tracking-[0.1em] text-charcoal/50">Version</dt>
              <dd className="mt-1 font-mono text-charcoal">v{fallbackBuildInfo.version}</dd>
            </div>
            <div>
              <dt className="font-semibold uppercase tracking-[0.1em] text-charcoal/50">Build</dt>
              <dd className="mt-1 font-mono text-charcoal">{fallbackBuildInfo.commit} · {fallbackBuildInfo.builtAt}</dd>
            </div>
            <div>
              <dt className="font-semibold uppercase tracking-[0.1em] text-charcoal/50">Environment</dt>
              <dd className="mt-1 font-mono text-charcoal">{fallbackBuildInfo.environment}</dd>
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
