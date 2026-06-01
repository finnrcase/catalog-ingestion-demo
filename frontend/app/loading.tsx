export default function Loading() {
  return (
    <main className="grid min-h-screen place-items-center px-6">
      <div className="w-full max-w-sm rounded-2xl border border-linen bg-white/70 p-6 text-center shadow-panel">
        <div className="mx-auto grid h-14 w-14 place-items-center rounded-2xl border border-orangeBorder bg-orangeSoft font-serif text-xl tracking-[0.18em] text-charcoal">
          SCH
        </div>
        <div className="mt-5 h-1.5 overflow-hidden rounded-full bg-ivory">
          <div className="h-full w-2/3 animate-pulse rounded-full bg-bronze" />
        </div>
        <p className="mt-4 text-sm font-semibold text-charcoal">Preparing DesignOps Intake</p>
        <p className="mt-1 text-xs text-taupe">Loading the SCH production workspace.</p>
      </div>
    </main>
  );
}
