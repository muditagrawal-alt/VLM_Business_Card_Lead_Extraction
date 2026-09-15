/**
 * Shared motion vocabulary.
 *
 * Two rules govern everything here, both from the Web Interface Guidelines:
 * only `transform` and `opacity` are animated, because they are the two
 * properties the compositor can handle without laying out the page again; and
 * a user who has asked for reduced motion gets none of it.
 *
 * Durations are short on purpose. This is a working tool — motion is here to
 * explain what changed, not to be noticed.
 */

import type { Transition, Variants } from 'motion/react';

/** Standard easing, matching the value O-HIVE's own stylesheet uses. */
export const EASE = [0.4, 0, 0.2, 1] as const;

export const FAST: Transition = { duration: 0.18, ease: EASE };
export const NORMAL: Transition = { duration: 0.28, ease: EASE };

/** Physical, for things the user has grabbed or dismissed. */
export const SPRING: Transition = { type: 'spring', stiffness: 380, damping: 32 };

/** Content arriving in place: a short rise, never a slide across the screen. */
export const riseIn: Variants = {
  hidden: { opacity: 0, y: 8 },
  visible: { opacity: 1, y: 0, transition: NORMAL },
};

/**
 * Staggers children by index.
 *
 * Capped deliberately: with a 50-card batch, a per-child delay would leave the
 * last row arriving seconds late. The cap keeps the effect legible on a small
 * batch and invisible on a large one.
 */
export function staggerAt(index: number, step = 0.03, cap = 0.24): Transition {
  return { ...NORMAL, delay: Math.min(index * step, cap) };
}

export const overlayFade: Variants = {
  hidden: { opacity: 0 },
  visible: { opacity: 1, transition: FAST },
  exit: { opacity: 0, transition: FAST },
};

export const drawerSlide: Variants = {
  hidden: { x: '100%' },
  visible: { x: 0, transition: SPRING },
  exit: { x: '100%', transition: { duration: 0.2, ease: EASE } },
};
