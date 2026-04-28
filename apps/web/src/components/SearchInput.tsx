"use client";

import { Search, X } from "lucide-react";
import { useId } from "react";

interface Props {
  value: string;
  onChange: (next: string) => void;
  placeholder?: string;
  className?: string;
  /** Icon-trigger size on mobile is unhelpful here; this stays an input
   * at every breakpoint, but we shrink the placeholder + max width. */
  ariaLabel?: string;
}

/** Pill-shaped search input — used in the secondary-row of both pages.
 * Controlled component, intentionally simple; the parent owns the filter
 * logic so the same input can drive a directory-rows filter or a
 * line-item-label filter without changes here. */
export default function SearchInput({
  value,
  onChange,
  placeholder = "Search…",
  className,
  ariaLabel,
}: Props) {
  const id = useId();
  return (
    <div
      className={
        "relative inline-flex items-center h-8 rounded-md border bg-background text-xs " +
        (className ?? "w-full max-w-xs")
      }
    >
      <Search className="size-3.5 absolute left-2.5 text-muted-foreground pointer-events-none" />
      <input
        id={id}
        type="search"
        value={value}
        onChange={(e) => onChange(e.target.value)}
        placeholder={placeholder}
        aria-label={ariaLabel ?? placeholder}
        className="w-full h-full bg-transparent pl-8 pr-7 outline-none placeholder:text-muted-foreground/80 focus-visible:ring-2 focus-visible:ring-ring/40 rounded-md"
      />
      {value && (
        <button
          type="button"
          aria-label="Clear search"
          onClick={() => onChange("")}
          className="absolute right-1.5 size-5 grid place-items-center text-muted-foreground hover:text-foreground rounded"
        >
          <X className="size-3" />
        </button>
      )}
    </div>
  );
}
