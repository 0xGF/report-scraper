"use client";

import { type CSSProperties, useEffect, useState } from "react";

interface Props {
  src: string | null | undefined;
  alt?: string;
  /** Tailwind size class — e.g. "size-9", "size-12". */
  size?: string;
  /** Tailwind rounded class — e.g. "rounded", "rounded-md", "rounded-lg". */
  rounded?: string;
  /** Two-letter ticker / slug fallback shown when `src` is empty / 404s. */
  fallback?: string;
  /** Allows callers to attach a `view-transition-name` for shared animations. */
  style?: CSSProperties;
  className?: string;
}

/**
 * Single source of truth for the company-logo treatment used by the
 * directory grid, the company hero, the search popover, and the logo
 * stack. Keeps the bordered + square + rounded look consistent.
 *
 * Robust fallback chain:
 *   1. Render `<img>` with `src` if provided.
 *   2. If the image fails to load (404, CORS, blocked), `onError` flips
 *      to the placeholder so users never see the broken-image icon.
 *   3. With no `src` (or after a load failure), show a soft-gradient tile
 *      with the ticker initials — looks intentional rather than empty.
 */
export default function LogoBox({
  src,
  alt = "",
  size = "size-9",
  rounded = "rounded-md",
  fallback,
  style,
  className,
}: Props) {
  const [errored, setErrored] = useState(false);
  // Reset the error state if the parent supplies a new `src` (e.g. after a
  // re-fetch); otherwise a one-time failure would stick across navigations.
  useEffect(() => {
    setErrored(false);
  }, [src]);

  const showImage = !!src && !errored;
  const initials = (fallback ?? "?").trim().slice(0, 2).toUpperCase() || "?";
  const baseClass =
    `${size} ${rounded} aspect-square border-2 border-zinc-100 bg-card overflow-hidden shrink-0 ` +
    (className ?? "");

  return (
    <div className={baseClass} style={style}>
      {showImage ? (
        /* eslint-disable-next-line @next/next/no-img-element */
        <img
          src={src ?? undefined}
          alt={alt}
          className="size-full object-cover"
          onError={() => setErrored(true)}
        />
      ) : (
        <div
          className="size-full grid place-items-center font-semibold text-muted-foreground bg-gradient-to-br from-zinc-50 to-zinc-200/80"
          aria-label={alt || initials}
          title={alt || initials}
        >
          <span className="text-[clamp(9px,40%,14px)] tracking-wide">
            {initials}
          </span>
        </div>
      )}
    </div>
  );
}
