import { describe, expect, it } from 'vitest';
import type { TierHealth } from '@/types/api';
import { describeInference, modelDisplayName } from './inference';

function tier(name: TierHealth['tier'], model: string | null): TierHealth {
  return { tier: name, healthy: true, breaker_state: 'closed', model };
}

describe('describeInference', () => {
  it('says nothing that could be false before the report arrives', () => {
    const copy = describeInference(undefined);
    expect(copy.reader).toBe('a vision-language model');
    expect(copy.disclosure).toBeNull();
  });

  it('describes a self-hosted Qwen deployment as self-hosted', () => {
    const copy = describeInference([
      tier('gpu', 'Qwen3VL-8B-Instruct-Q8_0'),
      tier('cpu', 'Qwen3VL-4B-Instruct-Q4_K_M'),
      tier('cloud', 'gemini-3.6-flash'),
    ]);
    // A hosted fallback behind self-hosted tiers is disclosed per row, not up front.
    expect(copy.hosted).toBe(false);
    expect(copy.reader).toBe('a self-hosted Qwen3-VL model');
    expect(copy.disclosure).toBeNull();
  });

  it('never claims self-hosting when every card goes to Gemini', () => {
    const copy = describeInference([tier('cloud', 'gemini-3.6-flash')]);
    expect(copy.hosted).toBe(true);
    expect(copy.reader).toBe("Google's Gemini 3.6 Flash");
    expect(copy.body).not.toMatch(/self-hosted|leaves the server unless/i);
    // The free tier's terms are what a person uploading real contacts needs to know.
    expect(copy.disclosure).toMatch(/Google may use submitted content/);
  });

  it('names Alibaba for a hosted Qwen model', () => {
    const copy = describeInference([tier('cloud', 'qwen3-vl-plus')]);
    expect(copy.disclosure).toBe('Cards uploaded here are sent to Alibaba Cloud to be read.');
  });

  it('falls back to a generic provider for an unknown hosted model', () => {
    const copy = describeInference([tier('cloud', 'some-vision-model')]);
    expect(copy.body).toContain('sent to a hosted provider');
  });
});

describe('modelDisplayName', () => {
  it.each([
    ['gemini-3.6-flash', 'Gemini 3.6 Flash'],
    ['gemini-3.1-flash-lite', 'Gemini 3.1 Flash Lite'],
    ['qwen3-vl-plus', 'Qwen3 VL Plus'],
  ])('%s reads as %s', (model, expected) => {
    expect(modelDisplayName(model)).toBe(expected);
  });
});
