/**
 * Application shell. The upload, job and history views are wired up in the
 * frontend phase; this shell establishes the layout and health indicator.
 */
export default function App() {
  return (
    <div className="flex min-h-full flex-col">
      <header className="border-b border-neutral-200 bg-white">
        <div className="mx-auto flex max-w-6xl items-center justify-between px-4 py-3">
          <h1 className="text-sm font-semibold tracking-tight">
            Business Card Lead Extraction
          </h1>
          <span className="text-xs text-neutral-500">Qwen3-VL</span>
        </div>
      </header>

      <main className="mx-auto w-full max-w-6xl flex-1 px-4 py-8">
        <p className="text-sm text-neutral-600">
          Upload business cards to extract structured leads.
        </p>
      </main>
    </div>
  );
}
