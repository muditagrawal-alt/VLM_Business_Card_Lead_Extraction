import imageCompression from 'browser-image-compression';
import { AlertCircle, ImagePlus, Loader2, X } from 'lucide-react';
import { useCallback, useState } from 'react';
import { useDropzone } from 'react-dropzone';
import { Button } from '@/components/ui/Button';
import { cn } from '@/lib/utils';

const MAX_FILES = 50;
const MAX_BYTES = 10 * 1024 * 1024;

// Cards are downscaled before upload: a modern phone photo is several
// megabytes, the server caps the long edge anyway, and shrinking in the
// browser is the difference between a fast upload and a stalled one on mobile.
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

interface Rejected {
  name: string;
  reason: string;
}

export function UploadZone({ onSubmit, busy }: Props) {
  const [files, setFiles] = useState<File[]>([]);
  const [rejected, setRejected] = useState<Rejected[]>([]);
  const [preparing, setPreparing] = useState(false);

  const onDrop = useCallback(
    async (accepted: File[], fileRejections: { file: File; errors: { message: string }[] }[]) => {
      setRejected(
        fileRejections.map((rejection) => ({
          name: rejection.file.name,
          reason: rejection.errors[0]?.message ?? 'this file cannot be used',
        })),
      );
      if (accepted.length === 0) return;

      setPreparing(true);
      try {
        const prepared = await Promise.all(
          accepted.map(async (file) => {
            try {
              return await imageCompression(file, {
                maxWidthOrHeight: CLIENT_MAX_EDGE,
                maxSizeMB: CLIENT_MAX_MB,
                useWebWorker: true,
                fileType: 'image/jpeg',
              });
            } catch {
              // HEIC and unusual encodings can defeat browser compression.
              // The server handles them, so send the original rather than
              // dropping a card the user chose.
              return file;
            }
          }),
        );
        setFiles((current) => [...current, ...prepared].slice(0, MAX_FILES));
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
    setFiles((current) => current.filter((_, i) => i !== index));

  return (
    <div className="space-y-4">
      <div
        {...getRootProps()}
        className={cn(
          'flex cursor-pointer flex-col items-center justify-center gap-3 rounded-lg border-2 border-dashed px-6 py-12 text-center transition-colors',
          isDragActive
            ? 'border-accent-500 bg-accent-50'
            : 'border-neutral-300 bg-white hover:border-neutral-400',
          (busy || preparing) && 'pointer-events-none opacity-60',
        )}
      >
        <input {...getInputProps()} />
        {preparing ? (
          <Loader2 className="size-7 animate-spin text-accent-600" aria-hidden />
        ) : (
          <ImagePlus className="size-7 text-neutral-400" aria-hidden />
        )}
        <div>
          <p className="text-sm font-medium text-neutral-800">
            {preparing ? 'Preparing images…' : 'Drop business cards here, or click to choose'}
          </p>
          <p className="mt-1 text-xs text-neutral-500">
            JPEG, PNG, WebP or HEIC · up to {MAX_FILES} cards · 10 MB each
          </p>
        </div>
      </div>

      {rejected.length > 0 && (
        <ul className="space-y-1 rounded-md border border-amber-200 bg-amber-50 p-3">
          {rejected.map((item) => (
            <li key={item.name} className="flex items-start gap-2 text-xs text-amber-900">
              <AlertCircle className="mt-0.5 size-3.5 shrink-0" aria-hidden />
              <span>
                <span className="font-medium">{item.name}</span> — {item.reason}
              </span>
            </li>
          ))}
        </ul>
      )}

      {files.length > 0 && (
        <>
          <ul className="grid grid-cols-2 gap-2 sm:grid-cols-3 lg:grid-cols-4">
            {files.map((file, index) => (
              <li
                key={`${file.name}-${index}`}
                className="group relative overflow-hidden rounded-md border border-neutral-200 bg-white"
              >
                <img
                  src={URL.createObjectURL(file)}
                  alt=""
                  className="h-24 w-full object-cover"
                  onLoad={(event) => URL.revokeObjectURL(event.currentTarget.src)}
                />
                <div className="truncate px-2 py-1.5 text-[11px] text-neutral-600">
                  {file.name}
                </div>
                <button
                  type="button"
                  onClick={() => remove(index)}
                  aria-label={`Remove ${file.name}`}
                  className="absolute right-1 top-1 rounded-full bg-white/90 p-1 text-neutral-700 opacity-0 transition-opacity group-hover:opacity-100 focus-visible:opacity-100"
                >
                  <X className="size-3.5" aria-hidden />
                </button>
              </li>
            ))}
          </ul>

          <div className="flex items-center justify-between">
            <p className="text-xs text-neutral-500">
              {files.length} {files.length === 1 ? 'card' : 'cards'} ready
            </p>
            <div className="flex gap-2">
              <Button variant="ghost" size="sm" onClick={() => setFiles([])} disabled={busy}>
                Clear
              </Button>
              <Button size="lg" onClick={() => onSubmit(files)} disabled={busy || preparing}>
                {busy ? 'Uploading…' : `Extract ${files.length} ${files.length === 1 ? 'card' : 'cards'}`}
              </Button>
            </div>
          </div>
        </>
      )}
    </div>
  );
}
