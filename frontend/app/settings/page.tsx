import Link from "next/link";

export default function SettingsPage() {
  const commit = (
    process.env.VERCEL_GIT_COMMIT_SHA ||
    process.env.NEXT_PUBLIC_VERCEL_GIT_COMMIT_SHA ||
    "local"
  ).slice(0, 12);
  const apiBase = process.env.NEXT_PUBLIC_API_BASE_URL || "not configured";

  return (
    <main className="min-h-screen px-4 py-6 sm:px-7 lg:px-10">
      <div className="mx-auto max-w-3xl rounded-[28px] border border-linen bg-white/70 p-6 shadow-panel">
        <div className="flex flex-col gap-4 border-b border-linen pb-5 sm:flex-row sm:items-center sm:justify-between">
          <div>
            <p className="text-xs font-semibold uppercase tracking-[0.22em] text-bronze">SCH Settings</p>
            <h1 className="mt-2 text-3xl font-semibold text-charcoal">Production frontend settings</h1>
            <p className="mt-2 text-sm leading-6 text-taupe">
              The full interactive settings panel lives in the main intake workspace header.
            </p>
          </div>
          <Link
            href="/"
            className="btn-primary inline-flex h-11 items-center justify-center rounded-xl px-5 text-sm font-semibold"
          >
            Open Intake
          </Link>
        </div>

        <dl className="mt-5 grid gap-3 text-sm">
          <div className="rounded-xl border border-linen bg-ivory/70 p-3">
            <dt className="text-xs font-semibold uppercase tracking-[0.12em] text-taupe">Commit</dt>
            <dd className="mt-1 font-mono text-charcoal">{commit}</dd>
          </div>
          <div className="rounded-xl border border-linen bg-ivory/70 p-3">
            <dt className="text-xs font-semibold uppercase tracking-[0.12em] text-taupe">API base</dt>
            <dd className="mt-1 break-all font-mono text-charcoal">{apiBase}</dd>
          </div>
          <div className="rounded-xl border border-linen bg-ivory/70 p-3">
            <dt className="text-xs font-semibold uppercase tracking-[0.12em] text-taupe">Homepage route</dt>
            <dd className="mt-1 font-mono text-charcoal">frontend/app/page.tsx</dd>
          </div>
        </dl>
      </div>
    </main>
  );
}
