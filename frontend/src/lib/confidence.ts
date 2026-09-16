import type { Lead, LeadField } from '@/types/api';

/** Below this, a value is worth a human glance before it is used. */
export const LOW_CONFIDENCE = 0.8;

/**
 * Explain, in plain words, why a field was flagged.
 *
 * The score alone tells a user nothing actionable. These messages name the
 * specific check that did not pass, because "the country code could not be
 * confirmed" leads somewhere and "confidence 0.7" does not.
 *
 * The thresholds mirror the scoring rules in the backend's normalisation
 * layer, where a merely-possible phone number is capped at 0.7, an
 * unparseable one at 0.4, and a value absent from the transcription scores
 * 0.6.
 */
export function explainConfidence(field: LeadField, lead: Lead): string | null {
  const score = lead.confidence?.[field];
  const value = lead[field];
  if (value === null || score === undefined || score >= LOW_CONFIDENCE) return null;

  if (field === 'phone') {
    if (score <= 0.4) {
      return 'This number could not be recognised as a valid phone number. It is shown exactly as printed on the card — check the digits before dialling.';
    }
    return 'The country code could not be confirmed, because the card does not print one and nothing else on it indicates a country. The digits are correct; the international prefix may not be.';
  }

  if (field === 'email') {
    return 'This address passed validation but does not appear in the text read from the card, so it may have been misread.';
  }

  if (field === 'location') {
    return 'The place name was assembled from the address and does not appear in the text read from the card. It may have been inferred rather than printed.';
  }

  return 'This value does not appear in the text read from the card, so it may have been misread. Open the card to compare.';
}
