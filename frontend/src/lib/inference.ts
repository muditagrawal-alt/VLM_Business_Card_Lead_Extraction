import type { TierHealth } from '@/types/api';

/** Wording that depends on what actually reads the cards on this deployment. */
export interface InferenceCopy {
  /** For sentences: "a self-hosted Qwen3-VL model", "Google's Gemini 3.6 Flash". */
  reader: string;
  /** Every tier is hosted, so every card leaves the server by design. */
  hosted: boolean;
  title: string;
  body: string;
  /** Shown beside the upload when cards are sent to a third party. */
  disclosure: string | null;
}

const NEUTRAL: InferenceCopy = {
  reader: 'a vision-language model',
  hosted: false,
  title: 'Vision-Language Model',
  body: 'A vision-language model reads each card directly. No separate OCR stage.',
  disclosure: null,
};

/** "gemini-3.6-flash" becomes "Gemini 3.6 Flash"; "qwen3-vl-plus" becomes "Qwen3 VL Plus". */
export function modelDisplayName(model: string): string {
  return model
    .split(/[-_\s]+/)
    .filter(Boolean)
    .map((word) =>
      word.toLowerCase() === 'vl' ? 'VL' : word.charAt(0).toUpperCase() + word.slice(1),
    )
    .join(' ');
}

function hostedCompany(model: string): string | null {
  if (/^gemini/i.test(model)) return 'Google';
  if (/^qwen/i.test(model)) return 'Alibaba Cloud';
  return null;
}

/**
 * Describe the deployment from its readiness report.
 *
 * The same build can front a self-hosted GPU stack or a deployment where every
 * card goes to a hosted API, so the page must not assume which. Until the
 * report arrives, or if it cannot be read, the wording says nothing that could
 * be false.
 */
export function describeInference(tiers: readonly TierHealth[] | undefined): InferenceCopy {
  if (!tiers || tiers.length === 0) return NEUTRAL;

  const local = tiers.find((t) => t.tier !== 'cloud');
  if (local) {
    const qwen = /qwen/i.test(local.model ?? '');
    return {
      reader: qwen ? 'a self-hosted Qwen3-VL model' : 'a self-hosted vision-language model',
      hosted: false,
      title: 'Self-Hosted Vision Model',
      body: `${qwen ? 'Qwen3-VL' : 'A vision-language model'} reads each card directly. No separate OCR stage, and no card leaves the server unless the hosted fallback is enabled.`,
      disclosure: null,
    };
  }

  const model = tiers[0]?.model;
  if (!model) return { ...NEUTRAL, hosted: true };
  const name = modelDisplayName(model);
  const company = hostedCompany(model);
  const destination = company ?? 'a hosted provider';
  return {
    reader: company === 'Google' ? `Google's ${name}` : name,
    hosted: true,
    title: `Read by ${name}`,
    body: `A vision-language model reads each card directly, with no separate OCR stage. On this deployment, cards are sent to ${destination} to be read.`,
    disclosure:
      company === 'Google'
        ? "Cards uploaded here are sent to Google's Gemini API to be read. On Gemini's free tier, Google may use submitted content to improve its products, so please upload sample cards rather than real contacts."
        : `Cards uploaded here are sent to ${destination} to be read.`,
  };
}
