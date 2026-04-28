"use client";

import { useEffect, useLayoutEffect, useRef, useState } from "react";
import { cn } from "@/lib/utils";

export interface SlidingTab<T extends string> {
  value: T;
  label: string;
  icon?: React.ReactNode;
}

interface Props<T extends string> {
  tabs: ReadonlyArray<SlidingTab<T>>;
  value: T;
  onChange: (next: T) => void;
  className?: string;
}

// Use `useLayoutEffect` on the client so the indicator is measured before
// paint (avoids a 1-frame flash at 0,0). Falls back to `useEffect` during
// SSR where layout hooks emit warnings.
const useIsomorphicLayoutEffect =
  typeof window !== "undefined" ? useLayoutEffect : useEffect;

/**
 * Lightweight tabs strip with a single bar that slides between options.
 * Active state is owned by the caller; we just measure the active button's
 * `offsetLeft` + `offsetWidth` and animate a positioned `<span>` to match.
 *
 * Why not the shadcn `Tabs` line variant: each tab there owns its own
 * `::after` underline, so the line snaps from one tab to another with no
 * tween. A single shared element gives the sliding effect.
 */
export default function SlidingTabs<T extends string>({
  tabs,
  value,
  onChange,
  className,
}: Props<T>) {
  const listRef = useRef<HTMLDivElement>(null);
  const buttonRefs = useRef(new Map<T, HTMLButtonElement>());
  const [indicator, setIndicator] = useState<{ x: number; w: number } | null>(
    null,
  );

  useIsomorphicLayoutEffect(() => {
    const btn = buttonRefs.current.get(value);
    const list = listRef.current;
    if (!btn || !list) return;
    const listRect = list.getBoundingClientRect();
    const btnRect = btn.getBoundingClientRect();
    setIndicator({ x: btnRect.left - listRect.left, w: btnRect.width });
  }, [value, tabs.length]);

  // Re-measure on resize so the indicator stays glued to its tab.
  useEffect(() => {
    if (typeof window === "undefined") return;
    const onResize = () => {
      const btn = buttonRefs.current.get(value);
      const list = listRef.current;
      if (!btn || !list) return;
      const listRect = list.getBoundingClientRect();
      const btnRect = btn.getBoundingClientRect();
      setIndicator({ x: btnRect.left - listRect.left, w: btnRect.width });
    };
    window.addEventListener("resize", onResize);
    return () => window.removeEventListener("resize", onResize);
  }, [value]);

  return (
    <div
      ref={listRef}
      role="tablist"
      className={cn(
        "relative inline-flex items-center gap-0.5 text-sm select-none",
        className,
      )}
    >
      {tabs.map((t) => {
        const active = t.value === value;
        return (
          <button
            key={t.value}
            ref={(el) => {
              if (el) buttonRefs.current.set(t.value, el);
              else buttonRefs.current.delete(t.value);
            }}
            type="button"
            role="tab"
            aria-selected={active}
            onClick={() => onChange(t.value)}
            className={cn(
              "relative z-10 inline-flex items-center gap-1.5 px-2.5 h-8 rounded-md transition-colors",
              active
                ? "text-foreground"
                : "text-muted-foreground hover:text-foreground",
            )}
          >
            {t.icon}
            <span className="font-medium">{t.label}</span>
          </button>
        );
      })}
      {/* Sliding pill — single element shared across tabs. transform +
          width animate via CSS. opacity-0 until measured to skip the
          first-frame flash. */}
      <span
        aria-hidden="true"
        className={cn(
          "pointer-events-none absolute inset-y-1 rounded-md bg-muted transition-[transform,width,opacity] duration-300 ease-[cubic-bezier(0.16,1,0.3,1)]",
          indicator ? "opacity-100" : "opacity-0",
        )}
        style={
          indicator
            ? {
                left: 0,
                transform: `translateX(${indicator.x}px)`,
                width: indicator.w,
              }
            : undefined
        }
      />
    </div>
  );
}
