"use client";

import { useEffect, useRef, useState } from "react";

interface Props {
  /** Final value to land on. */
  value: number;
  /** Animation duration in ms. */
  duration?: number;
  /** How many fractional digits to render. */
  decimals?: number;
  /** Optional thousand-separator locale. Defaults to en-US. */
  locale?: string;
  /** Render hook — wraps the formatted number with a prefix/suffix (e.g. "$"). */
  prefix?: string;
  suffix?: string;
}

const easeOutCubic = (t: number) => 1 - Math.pow(1 - t, 3);

/**
 * Animates a number from 0 → `value` on mount and any time `value` changes.
 * Uses `requestAnimationFrame` so the easing stays smooth at any frame rate.
 *
 * Respects `prefers-reduced-motion`: jumps straight to the final value when
 * the user has motion disabled.
 */
export default function CountUp({
  value,
  duration = 900,
  decimals = 0,
  locale = "en-US",
  prefix = "",
  suffix = "",
}: Props) {
  // Start at 0 so the first render always shows a count-up. SSR also
  // emits 0 (no animation server-side), then the client tweens from 0 →
  // value on mount. Subsequent re-renders animate from the previous
  // display value, captured by the closure inside the effect.
  const [display, setDisplay] = useState(0);
  const displayRef = useRef(0);
  displayRef.current = display;
  const startTsRef = useRef<number | null>(null);
  const rafRef = useRef<number | null>(null);

  useEffect(() => {
    if (typeof window === "undefined") return;
    const reduced = window.matchMedia(
      "(prefers-reduced-motion: reduce)",
    ).matches;
    if (reduced) {
      setDisplay(value);
      return;
    }

    const from = displayRef.current;
    startTsRef.current = null;

    const step = (ts: number) => {
      if (startTsRef.current === null) startTsRef.current = ts;
      const elapsed = ts - startTsRef.current;
      const t = Math.min(1, elapsed / duration);
      const eased = easeOutCubic(t);
      setDisplay(from + (value - from) * eased);
      if (t < 1) rafRef.current = requestAnimationFrame(step);
    };
    rafRef.current = requestAnimationFrame(step);
    return () => {
      if (rafRef.current !== null) cancelAnimationFrame(rafRef.current);
    };
  }, [value, duration]);

  const formatted = display.toLocaleString(locale, {
    minimumFractionDigits: decimals,
    maximumFractionDigits: decimals,
  });
  const target = value.toLocaleString(locale, {
    minimumFractionDigits: decimals,
    maximumFractionDigits: decimals,
  });
  // Reserve a fixed width based on the FINAL formatted string. Without
  // this, every frame re-measures (e.g. "1" → "12" → "123") and the row
  // layout judders as the count rises.
  const widthCh = `${prefix.length + target.length + suffix.length}ch`;
  return (
    <span
      className="tabular-nums inline-block text-right"
      style={{ minWidth: widthCh }}
    >
      {prefix}
      {formatted}
      {suffix}
    </span>
  );
}
