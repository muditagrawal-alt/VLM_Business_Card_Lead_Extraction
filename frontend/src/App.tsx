import { AnimatePresence, motion } from 'motion/react';
import { ThemeToggle } from '@/components/ThemeToggle';
import { useRoute } from '@/hooks/useRoute';
import { JobPage } from '@/pages/JobPage';
import { UploadPage } from '@/pages/UploadPage';
import { NORMAL } from '@/lib/motion';

export default function App() {
  const { jobId, go } = useRoute();

  return (
    <div className="flex min-h-full flex-col">
      <a
        href="#main"
        className="sr-only focus:not-sr-only focus:absolute focus:left-4 focus:top-4 focus:z-50 focus:rounded-md focus:bg-primary focus:px-3 focus:py-2 focus:text-sm focus:text-primary-foreground"
      >
        Skip to content
      </a>

      <header className="sticky top-0 z-30 border-b border-border bg-background/85 backdrop-blur">
        <div className="mx-auto flex max-w-6xl items-center justify-between gap-4 px-4 py-3">
          <a
            href="#"
            onClick={(event) => {
              event.preventDefault();
              go(null);
            }}
            className="flex items-center gap-2.5"
          >
            <span
              aria-hidden="true"
              className="flex size-6 items-center justify-center rounded bg-primary text-[11px] font-bold text-primary-foreground"
            >
              O
            </span>
            <span className="text-sm font-semibold tracking-tight" translate="no">
              Lead Extraction
            </span>
          </a>

          <nav className="flex items-center gap-1">
            <a
              href="/api/docs"
              target="_blank"
              rel="noreferrer"
              className="rounded-md px-2.5 py-1.5 text-xs text-muted-foreground transition-colors duration-150 hover:bg-accent hover:text-foreground"
            >
              API
            </a>
            <ThemeToggle />
          </nav>
        </div>
      </header>

      <main id="main" className="mx-auto w-full max-w-6xl flex-1 px-4 py-10">
        {/* Keyed on the route so each view animates in on navigation. */}
        <AnimatePresence mode="wait">
          <motion.div
            key={jobId ?? 'upload'}
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            exit={{ opacity: 0 }}
            transition={NORMAL}
          >
            {jobId ? (
              <JobPage jobId={jobId} onBack={() => go(null)} />
            ) : (
              <UploadPage onCreated={(id) => go(id)} />
            )}
          </motion.div>
        </AnimatePresence>
      </main>

      <footer className="border-t border-border px-4 py-4">
        <p className="mx-auto max-w-6xl text-xs text-muted-foreground">
          Cards are read by a self-hosted Qwen3-VL model and deleted after seven days.
        </p>
      </footer>
    </div>
  );
}
