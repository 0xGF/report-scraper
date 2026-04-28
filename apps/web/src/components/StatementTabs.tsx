"use client";

import { LineChart, Scale, Wallet } from "lucide-react";
import { useMemo, useState } from "react";
import HeaderCurrencySelect from "./HeaderCurrencySelect";
import SearchInput from "./SearchInput";
import SlidingTabs, { type SlidingTab } from "./SlidingTabs";
import StatementGrid from "./StatementGrid";
import { useCurrency } from "@/lib/useCurrency";
import { STATEMENT_LABELS, type Statement, type StatementDocument } from "@/lib/types";

interface Props {
  statements: Partial<Record<Statement, StatementDocument>>;
  fillHeight?: boolean;
}

const ORDER: Statement[] = ["income", "balance", "cashflow"];

// Icons: revenue trend → LineChart, assets/liabilities balanced → Scale,
// cash movement → Wallet.
const STATEMENT_ICONS: Record<Statement, React.ReactNode> = {
  income: <LineChart className="size-3.5" />,
  balance: <Scale className="size-3.5" />,
  cashflow: <Wallet className="size-3.5" />,
};

export default function StatementTabs({ statements, fillHeight = false }: Props) {
  const available = ORDER.filter((k) => statements[k] != null);
  const [active, setActive] = useState<Statement>(available[0] ?? "income");
  const [displayCurrency] = useCurrency();
  const [query, setQuery] = useState("");
  const doc = statements[active];

  // Filter rows by canonical_name. Section headers and direct ancestors of
  // a matched row stay visible so context isn't lost in the filtered view.
  const filteredDoc = useMemo<StatementDocument | undefined>(() => {
    if (!doc) return doc;
    if (!query.trim()) return doc;
    const needle = query.toLowerCase();
    const matched = new Set<number>();
    doc.rows.forEach((r, i) => {
      if (r.canonical_name.toLowerCase().includes(needle)) matched.add(i);
    });
    if (matched.size === 0) return { ...doc, rows: [] };
    // Walk back from each match to keep section headers (depth ascending).
    const keep = new Set<number>(matched);
    for (const idx of matched) {
      const row = doc.rows[idx];
      if (!row) continue;
      const targetDepth = row.depth;
      for (let j = idx - 1; j >= 0; j--) {
        const r = doc.rows[j];
        if (!r) break;
        if (r.is_header && r.depth < targetDepth) {
          keep.add(j);
        }
      }
    }
    const orderedRows = doc.rows.filter((_, i) => keep.has(i));
    return { ...doc, rows: orderedRows };
  }, [doc, query]);

  const tabs: SlidingTab<Statement>[] = available.map((k) => ({
    value: k,
    label: STATEMENT_LABELS[k],
    icon: STATEMENT_ICONS[k],
  }));

  return (
    <div
      className={
        fillHeight ? "flex-1 min-h-0 flex flex-col" : "flex flex-col"
      }
    >
      {/* Same `h-12 border-b` shell as the home-page lower bar — the two
          rows should line up exactly when navigating between pages. */}
      <div className="px-4 sm:px-6 h-12 flex items-center gap-3 sm:gap-4 border-b">
        <SlidingTabs
          tabs={tabs}
          value={active}
          onChange={(v) => {
            setActive(v);
            setQuery("");
          }}
        />
        <SearchInput
          value={query}
          onChange={setQuery}
          placeholder="Filter line items…"
          className="ml-auto w-full max-w-xs"
        />
        <div className="shrink-0">
          <HeaderCurrencySelect />
        </div>
      </div>
      {filteredDoc && (
        <div className={fillHeight ? "flex-1 min-h-0 flex flex-col" : "flex flex-col"}>
          <StatementGrid
            statement={filteredDoc}
            fillHeight={fillHeight}
            displayCurrency={displayCurrency}
          />
        </div>
      )}
    </div>
  );
}
