import { JobPage } from '@/pages/JobPage';
import { UploadPage } from '@/pages/UploadPage';
import { useRoute } from '@/hooks/useRoute';

export default function App() {
  const { jobId, go } = useRoute();

  return (
    <div className="flex min-h-full flex-col">
      <header className="border-b border-neutral-200 bg-white">
        <div className="mx-auto flex max-w-6xl items-center justify-between gap-4 px-4 py-3">
          <button
            type="button"
            onClick={() => go(null)}
            className="text-sm font-semibold tracking-tight text-neutral-900"
          >
            Business Card Lead Extraction
          </button>
          <a
            href="/api/docs"
            className="text-xs text-neutral-500 hover:text-neutral-800"
            target="_blank"
            rel="noreferrer"
          >
            API
          </a>
        </div>
      </header>

      <main className="mx-auto w-full max-w-6xl flex-1 px-4 py-8">
        {jobId ? (
          <JobPage jobId={jobId} onBack={() => go(null)} />
        ) : (
          <UploadPage onCreated={(id) => go(id)} />
        )}
      </main>

      <footer className="border-t border-neutral-200 px-4 py-3">
        <p className="mx-auto max-w-6xl text-xs text-neutral-400">
          Cards are processed by a self-hosted Qwen3-VL model and deleted after seven
          days.
        </p>
      </footer>
    </div>
  );
}
