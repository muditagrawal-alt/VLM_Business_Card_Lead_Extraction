import imageCompression from 'browser-image-compression';
import { AnimatePresence, motion, useReducedMotion } from 'motion/react';
import { AlertCircle, ImagePlus, X } from 'lucide-react';
import { useCallback, useEffect, useState } from 'react';
import { useDropzone } from 'react-dropzone';
import { Button } from '@/components/ui/Button';
import { Spinner } from '@/components/ui/Spinner';
import { FAST, staggerAt } from '@/lib/motion';
import { cn } from '@/lib/utils';

const MAX_FILES = 50;
const MAX_BYTES = 10 * 1024 * 1024;

// Cards are downscaled before upload: a phone photo is several megabytes, the
// server caps the long edge anyway, and shrinking in the browser is the
// difference between a fast upload and a stalled one on mobile data.
const CLIENT_MAX_EDGE = 1600;
const CLIENT_MAX_MB = 2;

const ACCEPTED = {
  'image/jpeg': ['.jpg', '.jpeg'],
  'image/png': ['.png'],
  'image/webp': ['.webp'],
  'image/heic': ['.heic'],
  'image/heif': ['.heif'],
};

interface Props {
  onSubmit: (files: File[]) => void;
  busy: boolean;
}

interface Staged {
  file: File;
  previewUrl: string;
}

export function UploadZone({ onSubmit, busy }: Props) {
  const reduced = useReducedMotion();
  const [staged, setStaged] = useState<Staged[]>([]);
  const [rejected, setRejected] = useState<{ name: string; reason: string }[]>([]);
  const [preparing, setPreparing] = useState(false);

  // Object URLs are revoked on unmount rather than on image load: the preview
  // is re-rendered whenever the list changes, and a revoked URL would show a
  // broken image the second time round.
  useEffect(
    () => () => {
      for (const item of staged) URL.revokeObjectURL(item.previewUrl);
    },
    [staged],
  );

  const onDrop = useCallback(
    async (
      accepted: File[],
      fileRejections: { file: File; errors: { message: string }[] }[],
    ) => {
      setRejected(
        fileRejections.map((rejection) => ({
          name: rejection.file.name,
          reason: rejection.errors[0]?.message ?? 'This file cannot be used.',
        })),
      );
      if (accepted.length === 0) return;

      setPreparing(true);
      try {
        const prepared = await Promise.all(
          accepted.map(async (file) => {
            let out = file;
            try {
              out = await imageCompression(file, {
                maxWidthOrHeight: CLIENT_MAX_EDGE,
                maxSizeMB: CLIENT_MAX_MB,
                useWebWorker: true,
                fileType: 'image/jpeg',
              });
            } catch {
              // HEIC and unusual encodings can defeat browser compression.
              // The server handles them, so send the original rather than
              // dropping a card the user deliberately chose.
            }
            return { file: out, previewUrl: URL.createObjectURL(out) };
          }),
        );
        setStaged((current) => [...current, ...prepared].slice(0, MAX_FILES));
      } finally {
        setPreparing(false);
      }
    },
    [],
  );

  const { getRootProps, getInputProps, isDragActive } = useDropzone({
    onDrop: onDrop as never,
    accept: ACCEPTED,
    maxSize: MAX_BYTES,
    maxFiles: MAX_FILES,
    disabled: busy || preparing,
  });

  const remove = (index: number) =>
    setStaged((current) => {
      const item = current[index];
      if (item) URL.revokeObjectURL(item.previewUrl);
      return current.filter((_, i) => i !== index);
    });

  const clear = () =>
    setStaged((current) => {
      for (const item of current) URL.revokeObjectURL(item.previewUrl);
      return [];
    });

  const count = staged.length;

  return (
    <div className="space-y-5">
      <div
        {...getRootProps()}
        className={cn(
          'group relative flex cursor-pointer flex-col items-center justify-center gap-3',
          'rounded-xl border border-dashed px-6 py-14 text-center',
          'transition-[background-color,border-color] duration-200',
          isDragActive
            ? 'border-primary/40 bg-accent'
            : 'border-border bg-card hover:border-muted-foreground/40 hover:bg-accent/40',
          (busy || preparing) && 'pointer-events-none opacity-60',
        )}
      >
        <input {...getInputProps()} />

        <motion.div
          animate={reduced ? {} : { y: isDragActive ? -3 : 0 }}
          transition={FAST}
          className="flex size-11 items-center justify-center rounded-lg bg-muted"
        >
          {preparing ? (
            <Spinner className="text-muted-foreground" />
          ) : (
            <ImagePlus className="size-5 text-muted-foreground" aria-hidden="true" />
          )}
        </motion.div>

        <div className="space-y-1">
          <p className="text-sm font-medium">
            {preparing ? 'Preparing Images…' : 'Drop Business Cards Here'}
          </p>
          <p className="text-xs text-muted-foreground">
            or click to choose — JPEG, PNG, WebP or HEIC, up to {MAX_FILES}
            &nbsp;cards at 10&nbsp;MB each
          </p>
        </div>
      </div>

      <AnimatePresence mode="popLayout">
        {rejected.length > 0 && (
          <motion.ul
            initial={{ opacity: 0, height: 0 }}
            animate={{ opacity: 1, height: 'auto' }}
            exit={{ opacity: 0, height: 0 }}
            transition={FAST}
            aria-live="polite"
            className="space-y-1.5 overflow-hidden rounded-lg border border-warning/30 bg-warning-surface p-3"
          >
            {rejected.map((item) => (
              <li
                key={item.name}
                className="flex items-start gap-2 text-xs text-warning-foreground"
              >
                <AlertCircle className="mt-0.5 size-3.5 shrink-0" aria-hidden="true" />
                <span className="min-w-0">
                  <span className="font-medium">{item.name}</span> — {item.reason}
                </span>
              </li>
            ))}
          </motion.ul>
        )}
      </AnimatePresence>

      <AnimatePresence>
        {count > 0 && (
          <motion.div
            initial={{ opacity: 0, y: 8 }}
            animate={{ opacity: 1, y: 0 }}
            exit={{ opacity: 0 }}
            transition={FAST}
            className="space-y-4"
          >
            <ul className="grid grid-cols-2 gap-2.5 sm:grid-cols-3 lg:grid-cols-4">
              <AnimatePresence mode="popLayout">
                {staged.map((item, index) => (
                  <motion.li
                    key={item.previewUrl}
                    layout
                    initial={{ opacity: 0, scale: 0.94 }}
                    animate={{ opacity: 1, scale: 1 }}
                    exit={{ opacity: 0, scale: 0.94 }}
                    transition={staggerAt(index)}
                    className="group/card relative overflow-hidden rounded-lg border border-border bg-card"
                  >
                    <img
                      src={item.previewUrl}
                      alt=""
                      width={320}
                      height={200}
                      className="h-24 w-full object-cover"
                    />
                    <p className="truncate px-2 py-1.5 text-[11px] text-muted-foreground">
                      {item.file.name}
                    </p>
                    <button
                      type="button"
                      onClick={() => remove(index)}
                      aria-label={`Remove ${item.file.name}`}
                      className="absolute right-1.5 top-1.5 rounded-full bg-background/90 p-1 text-foreground opacity-0 transition-opacity duration-150 group-hover/card:opacity-100 focus-visible:opacity-100"
                    >
                      <X className="size-3.5" aria-hidden="true" />
                    </button>
                  </motion.li>
                ))}
              </AnimatePresence>
            </ul>

            <div className="flex flex-wrap items-center justify-between gap-3">
              <p className="tnum text-xs text-muted-foreground">
                {count} {count === 1 ? 'card' : 'cards'} ready
              </p>
              <div className="flex gap-2">
                <Button variant="ghost" size="sm" onClick={clear} disabled={busy}>
                  Clear
                </Button>
                <Button
                  size="lg"
                  onClick={() => onSubmit(staged.map((item) => item.file))}
                  disabled={busy || preparing}
                >
                  {busy ? <Spinner /> : null}
                  {busy ? 'Uploading…' : `Extract ${count} ${count === 1 ? 'Card' : 'Cards'}`}
                </Button>
              </div>
            </div>
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  );
}
