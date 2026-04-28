"use client";

import {
  AllCommunityModule,
  ModuleRegistry,
  themeQuartz,
  type ColDef,
  type ICellRendererParams,
  type RowClickedEvent,
} from "ag-grid-community";
import { AgGridReact } from "ag-grid-react";
import { useTransitionRouter } from "next-view-transitions";
import { useMemo } from "react";
import LogoBox from "./LogoBox";
import Sparkline from "./Sparkline";
import { fxRate } from "@/lib/fx";
import { formatMarketCap, formatPrice } from "@/lib/format";
import { useCurrency } from "@/lib/useCurrency";

ModuleRegistry.registerModules([AllCommunityModule]);

const directoryTheme = themeQuartz.withParams({
  backgroundColor: "#ffffff",
  foregroundColor: "#1a1a1a",
  headerBackgroundColor: "#f7f7f7",
  headerTextColor: "#666666",
  borderColor: "#e6e6e6",
  rowHoverColor: "#f7f7f7",
  selectedRowBackgroundColor: "#efefef",
  oddRowBackgroundColor: "#ffffff",
  rowBorder: { style: "solid", width: 1, color: "#f0f0f0" },
  headerRowBorder: { style: "solid", width: 1, color: "#e6e6e6" },
  // Grid bleeds full width to the viewport edges, so a rounded outer
  // corner would clip awkwardly against the page edge. Square corners +
  // no outer wrapper border (the row borders + header line do the work).
  wrapperBorder: false,
  borderRadius: 0,
  rowHeight: 56,
  headerHeight: 36,
  fontSize: 13,
  fontFamily: "var(--font-sans)",
});

export interface DirectoryRow {
  slug: string;
  name: string;
  ticker: string;
  domain: string;
  industry: string;
  headquarters: string;
  founded: number;
  logoSrc: string;
  periods: string[];
  statements: string[]; // ["income","balance","cashflow"]
  /** False for newly-discovered companies that haven't been scraped/parsed
   *  yet. Rows render dimmed with a "pipeline pending" hint and don't
   *  navigate on click. */
  hasData: boolean;
  // Live market snapshot — null when `report-scrape market` hasn't run / Yahoo errored.
  price: number | null;
  priceCurrency: string | null;
  changePct: number | null;
  marketCap: number | null;
  sparkline: number[];
}

interface Props {
  rows: DirectoryRow[];
}

function periodSpan(periods: readonly string[]): string {
  const first = periods[0];
  const last = periods[periods.length - 1];
  if (!first || !last) return "—";
  return `${last.slice(0, 4)}–${first.slice(0, 4)} · ${periods.length} yrs`;
}

const STATEMENT_LABELS: Record<string, string> = {
  income: "IS",
  balance: "BS",
  cashflow: "CF",
};

export default function CompanyDirectory({ rows }: Props) {
  // useTransitionRouter wraps router.push in document.startViewTransition
  // and waits for the new route to commit before snapshotting the new
  // frame — that's what makes the shared `view-transition-name` actually
  // animate between pages.
  const router = useTransitionRouter();
  // Display currency is global (localStorage-backed). Price + market cap
  // get converted from each row's source currency into this one before
  // rendering. Sparklines stay shape-only so the rate doesn't matter there.
  const [displayCurrency] = useCurrency();

  const columns = useMemo<ColDef<DirectoryRow>[]>(
    () => [
      {
        colId: "logo",
        headerName: "",
        width: 64,
        minWidth: 64,
        maxWidth: 64,
        sortable: false,
        cellRenderer: (p: ICellRendererParams<DirectoryRow>) => {
          if (!p.data) return null;
          // Pairs with the matching `view-transition-name` on the destination
          // hero — the browser animates position + scale on navigation.
          const transitionName = `company-logo-${p.data.slug}`;
          return (
            <div className="flex items-center justify-center h-full">
              <LogoBox
                src={p.data.logoSrc}
                alt={`${p.data.name} logo`}
                size="size-9"
                rounded="rounded-md"
                fallback={p.data.ticker}
                style={{ viewTransitionName: transitionName }}
              />
            </div>
          );
        },
      },
      {
        colId: "name",
        headerName: "Company",
        field: "name",
        flex: 2,
        minWidth: 180,
        cellRenderer: (p: ICellRendererParams<DirectoryRow>) => {
          if (!p.data) return null;
          return (
            <div className="flex flex-col justify-center h-full">
              <div className="font-medium leading-tight">{p.data.name}</div>
              <div className="text-xs text-muted-foreground font-mono leading-tight mt-0.5">
                {p.data.ticker} · {p.data.domain}
              </div>
            </div>
          );
        },
      },
      {
        colId: "industry",
        headerName: "Industry",
        field: "industry",
        flex: 1.5,
        minWidth: 150,
      },
      {
        colId: "hq",
        headerName: "Headquarters",
        field: "headquarters",
        flex: 1.5,
        minWidth: 150,
      },
      {
        colId: "founded",
        headerName: "Founded",
        field: "founded",
        width: 90,
      },
      {
        colId: "price",
        headerName: "Price",
        width: 100,
        sortable: true,
        type: "rightAligned",
        // valueGetter returns the converted number so sort stays correct
        // when display currency changes.
        valueGetter: (p) => {
          const d = p.data;
          if (!d || d.price == null) return null;
          return d.price * fxRate(d.priceCurrency ?? "EUR", displayCurrency);
        },
        cellRenderer: (p: ICellRendererParams<DirectoryRow>) => {
          const data = p.data;
          if (!data) return null;
          const change = data.changePct;
          const changeColor =
            change == null
              ? "text-muted-foreground/60"
              : change >= 0
                ? "text-green-600"
                : "text-red-600";
          const converted =
            data.price == null
              ? null
              : data.price * fxRate(data.priceCurrency ?? "EUR", displayCurrency);
          return (
            <div className="flex flex-col items-end justify-center h-full leading-tight tabular-nums">
              <div className="text-foreground">
                {formatPrice(converted, displayCurrency)}
              </div>
              {change != null && (
                <div className={`text-[10px] ${changeColor}`}>
                  {change >= 0 ? "+" : ""}
                  {change.toFixed(2)}%
                </div>
              )}
            </div>
          );
        },
      },
      {
        colId: "marketCap",
        headerName: "Market cap",
        width: 110,
        sortable: true,
        type: "rightAligned",
        valueGetter: (p) => {
          const d = p.data;
          if (!d || d.marketCap == null) return null;
          return d.marketCap * fxRate(d.priceCurrency ?? "EUR", displayCurrency);
        },
        valueFormatter: (p) => formatMarketCap(p.value, displayCurrency),
        cellStyle: { textAlign: "right" },
      },
      {
        colId: "sparkline",
        headerName: "3 mo",
        width: 110,
        sortable: false,
        cellRenderer: (p: ICellRendererParams<DirectoryRow>) => {
          if (!p.data) return null;
          return (
            <div className="flex items-center justify-center h-full">
              <Sparkline values={p.data.sparkline} />
            </div>
          );
        },
      },
      {
        colId: "coverage",
        headerName: "Coverage",
        flex: 1.2,
        minWidth: 140,
        sortable: false,
        cellRenderer: (p: ICellRendererParams<DirectoryRow>) => {
          if (!p.data) return null;
          if (!p.data.hasData) {
            return (
              <span className="text-xs text-amber-600/90 font-medium">
                Pipeline pending
              </span>
            );
          }
          return periodSpan(p.data.periods);
        },
      },
      {
        colId: "statements",
        headerName: "Statements",
        width: 130,
        sortable: false,
        cellRenderer: (p: ICellRendererParams<DirectoryRow>) => {
          const data = p.data;
          if (!data) return null;
          return (
            <div className="flex items-center gap-2 h-full font-mono text-xs">
              {(["income", "balance", "cashflow"] as const).map((s) => (
                <span
                  key={s}
                  className={
                    data.statements.includes(s)
                      ? "text-foreground"
                      : "text-muted-foreground/25"
                  }
                >
                  {STATEMENT_LABELS[s]}
                </span>
              ))}
            </div>
          );
        },
      },
    ],
    // Rebuild column defs when display currency changes so the price /
    // market-cap cells re-render with the converted values + new glyph.
    [displayCurrency],
  );

  return (
    <div className="flex-1 min-h-0">
      <AgGridReact<DirectoryRow>
        theme={directoryTheme}
        rowData={rows}
        columnDefs={columns}
        suppressCellFocus
        suppressColumnVirtualisation
        animateRows={false}
        getRowClass={(p) =>
          p.data?.hasData ? "cursor-pointer" : "cursor-default opacity-55"
        }
        onRowClicked={(e: RowClickedEvent<DirectoryRow>) => {
          // Pending rows have no statement pages to navigate to.
          if (e.data?.hasData) router.push(`/c/${e.data.slug}`);
        }}
        defaultColDef={{
          resizable: true,
          sortable: true,
          suppressHeaderMenuButton: true,
        }}
      />
    </div>
  );
}
