import { describe, expect, it } from 'vitest';
import { cn } from './utils';

describe('cn', () => {
  it('drops falsy conditional classes', () => {
    const active = false;
    expect(cn('a', active && 'b', 'c')).toBe('a c');
  });

  it('lets a later Tailwind utility win', () => {
    // Without tailwind-merge both padding classes would survive and the
    // outcome would depend on stylesheet order.
    expect(cn('px-2', 'px-4')).toBe('px-4');
  });
});
