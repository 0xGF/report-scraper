"use client";

import { Check, Copy, Plus } from "lucide-react";
import { useMemo, useState } from "react";
import CompanyDirectory, { type DirectoryRow } from "./CompanyDirectory";
import HeaderCurrencySelect from "./HeaderCurrencySelect";
import SearchInput from "./SearchInput";

interface Props {
  rows: DirectoryRow[];
}

function matchesQuery(row: DirectoryRow, q: string): boolean {
  if (!q) return true;
  const needle = q.toLowerCase();
  return (
    row.name.toLowerCase().includes(needle) ||
    row.ticker.toLowerCase().includes(needle) ||
    row.industry.toLowerCase().includes(needle) ||
    row.headquarters.toLowerCase().includes(needle) ||
    row.slug.toLowerCase().includes(needle)
  );
}

/**
 * Wraps the AG Grid directory with a small search input above it. Filtering
 * happens client-side (the dataset is tiny — a handful of companies — so
 * reactive filtering on every keystroke is essentially free).
 */
export default function CompanyDirectoryClient({ rows }: Props) {
  const [query, setQuery] = useState("");
  const visibleRows = useMemo(
    () => rows.filter((r) => matchesQuery(r, query)),
    [rows, query],
  );
  const matchInfo =
    query && visibleRows.length !== rows.length
      ? `${visibleRows.length}/${rows.length}`
      : `${rows.length} companies`;

  return (
    <div className="flex-1 min-h-0 flex flex-col">
      {/* Lower bar — same height/padding as the company-page lower bar so the
          two layouts line up exactly when you navigate between them. */}
      <div className="px-4 sm:px-6 h-12 border-b flex items-center gap-3 sm:gap-4">
        <SearchInput
          value={query}
          onChange={setQuery}
          placeholder="Search companies, tickers, industries…"
          className="w-full max-w-md"
        />
        <span className="text-xs text-muted-foreground tabular-nums shrink-0 hidden sm:inline">
          {matchInfo}
        </span>
        <div className="ml-auto shrink-0">
          <HeaderCurrencySelect />
        </div>
      </div>
      <div className="flex-1 min-h-0 flex flex-col">
        <CompanyDirectory rows={visibleRows} />
      </div>
      <DiscoverFooter />
    </div>
  );
}

/** Bottom-of-list "add another" callout. `report-scrape add` chains everything —
 * discovery, logo download, scrape, parse, confirm, normalize, export,
 * market refresh — into one command. Click the chip to copy. */
function DiscoverFooter() {
  return (
    <div className="border-t bg-muted/40 px-4 sm:px-6 py-3 flex flex-wrap items-center gap-3">
      <div className="flex items-center gap-2 text-xs text-muted-foreground">
        <Plus className="size-3.5" />
        <span>Add another company:</span>
      </div>
      <CopyableCommand command='uv run report-scrape add "Company"' />
      <span className="text-[11px] text-muted-foreground/70">
        Replace &ldquo;Company&rdquo; with the name; everything else is automatic.
      </span>
    </div>
  );
}

function CopyableCommand({ command }: { command: string }) {
  const [copied, setCopied] = useState(false);

  const handleCopy = () => {
    if (typeof navigator === "undefined" || !navigator.clipboard) return;
    navigator.clipboard
      .writeText(command)
      .then(() => {
        setCopied(true);
        // Revert the icon after the user's eyes have caught the check.
        window.setTimeout(() => setCopied(false), 1400);
      })
      .catch(() => {});
  };

  return (
    <button
      type="button"
      onClick={handleCopy}
      title={copied ? "Copied!" : "Click to copy"}
      aria-label={copied ? "Copied to clipboard" : "Copy command"}
      // Slightly darker than the row borders / muted gray, but well below
      // pure black so it doesn't dominate the footer strip.
      className="group inline-flex items-center gap-2 rounded-md bg-zinc-700 text-zinc-50 px-2.5 py-1 font-mono text-[11px] transition-colors hover:bg-zinc-800"
    >
      <span>{command}</span>
      <span className="relative inline-flex items-center justify-center size-3.5">
        <Copy
          className={
            "size-3.5 absolute transition-all duration-200 " +
            (copied ? "opacity-0 scale-75" : "opacity-100 scale-100")
          }
        />
        <Check
          className={
            "size-3.5 absolute transition-all duration-200 text-emerald-400 " +
            (copied ? "opacity-100 scale-100" : "opacity-0 scale-75")
          }
        />
      </span>
    </button>
  );
}
