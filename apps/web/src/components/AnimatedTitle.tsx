"use client";

import { useEffect, useState } from "react";
import { cn } from "@/lib/utils";

interface Props {
  text: string;
  /** Per-letter delay in ms. */
  stagger?: number;
  className?: string;
}

/**
 * Drops each letter of the title in from above with a small stagger.
 *
 * Notes:
 * - Letters render as inline-blocks so `transform: translateY` works on each.
 * - Spaces become non-breaking spaces so they claim width while letters
 *   are still translated above and wouldn't otherwise occupy the gap.
 * - Respects `prefers-reduced-motion`: skips the animation entirely.
 */
export default function AnimatedTitle({
  text,
  stagger = 35,
  className,
}: Props) {
  const reduced = useReducedMotion();
  const letters = Array.from(text);
  const dropDuration = 380;

  return (
    <span
      className={cn("inline-block relative align-baseline", className)}
      // Real text content for screen readers / copy-paste; the per-letter
      // spans are aria-hidden.
      aria-label={text}
    >
      {letters.map((ch, i) => (
        <span
          key={`${ch}-${i}`}
          aria-hidden="true"
          className="inline-block whitespace-pre"
          style={
            reduced
              ? undefined
              : {
                  animation: `animated-title-drop ${dropDuration}ms cubic-bezier(0.16, 1, 0.3, 1) backwards`,
                  animationDelay: `${i * stagger}ms`,
                }
          }
        >
          {ch === " " ? "\u00a0" : ch}
        </span>
      ))}
    </span>
  );
}

function useReducedMotion(): boolean {
  const [reduced, setReduced] = useState(false);
  useEffect(() => {
    if (typeof window === "undefined") return;
    const mq = window.matchMedia("(prefers-reduced-motion: reduce)");
    setReduced(mq.matches);
    const listener = (e: MediaQueryListEvent) => setReduced(e.matches);
    mq.addEventListener("change", listener);
    return () => mq.removeEventListener("change", listener);
  }, []);
  return reduced;
}
