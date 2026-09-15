import { Moon, Sun } from 'lucide-react';
import { motion } from 'motion/react';
import { Button } from '@/components/ui/Button';
import { useTheme } from '@/hooks/useTheme';
import { FAST } from '@/lib/motion';

export function ThemeToggle() {
  const { theme, cycle } = useTheme();
  const isDark =
    theme === 'dark' ||
    (theme === 'system' &&
      typeof window !== 'undefined' &&
      window.matchMedia('(prefers-color-scheme: dark)').matches);

  return (
    <Button
      variant="ghost"
      size="icon"
      onClick={cycle}
      aria-label={isDark ? 'Switch to light theme' : 'Switch to dark theme'}
    >
      <motion.span
        key={isDark ? 'dark' : 'light'}
        initial={{ opacity: 0, rotate: -35 }}
        animate={{ opacity: 1, rotate: 0 }}
        transition={FAST}
        className="flex"
      >
        {isDark ? (
          <Moon className="size-4" aria-hidden="true" />
        ) : (
          <Sun className="size-4" aria-hidden="true" />
        )}
      </motion.span>
    </Button>
  );
}
