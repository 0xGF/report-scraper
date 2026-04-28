"use client";

import {
  AllCommunityModule,
  ModuleRegistry,
  themeQuartz,
  type ColDef,
  type ValueFormatterParams,
  type ValueGetterParams,
} from "ag-grid-community";
import { AgGridReact } from "ag-grid-react";
import { useMemo } from "react";
import { CURRENCY_GLYPH, fxRate, type DisplayCurrency } from "@/lib/fx";
import type { Cell, Row, StatementDocument } from "@/lib/types";

ModuleRegistry.registerModules([AllCommunityModule]);

// Mirrors `directoryTheme` (CompanyDirectory) so both grids share the same
// gray header band, border colors, hover state, and overall feel. Statement
// rows stay shorter (32px vs 56px) because financial line lists are dense.
const gridTheme = themeQuartz.withParams({
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
  wrapperBorder: false,
  headerColumnBorder: false,
  cellHorizontalPadding: 14,
  rowHeight: 32,
  headerHeight: 36,
  fontSize: 13,
  fontFamily: "var(--font-sans)",
});

function formatPeriodHeader(periodEnd: string): string {
  const d = new Date(periodEnd);
  if (Number.isNaN(d.getTime())) return periodEnd;
  const monthName = d.toLocaleString("en-US", { month: "short", timeZone: "UTC" });
  const day = d.getUTCDate();
  const year = d.getUTCFullYear();
  return `${monthName} ${day}, ${year}`;
}

function makeFormatter(glyph: string) {
  return function formatNumber(v: number | null | undefined): string {
    if (v === null || v === undefined) return "—";
    if (v === 0) return `${glyph}0`;
    const abs = Math.abs(v);
    const s = abs.toLocaleString("en-US", {
      maximumFractionDigits: 2,
      minimumFractionDigits: 0,
    });
    return v < 0 ? `-${glyph}${s}` : `${glyph}${s}`;
  };
}

function getCell(row: Row | undefined, period: string): Cell | undefined {
  return row?.values?.[period];
}

function rowClassFor(row: Row | undefined): string {
  if (!row) return "";
  if (row.is_header) return "row-header";
  if (row.is_total && row.depth === 0) return "row-total row-total-grand";
  if (row.is_total) return "row-total";
  return "";
}

interface Props {
  statement: StatementDocument;
  fillHeight?: boolean;
  displayCurrency?: DisplayCurrency;
}

export default function StatementGrid({
  statement,
  fillHeight = false,
  displayCurrency,
}: Props) {
  const sourceCurrency = (statement.currency ?? "EUR").toUpperCase();
  const targetCurrency: DisplayCurrency =
    displayCurrency ??
    (sourceCurrency === "USD" || sourceCurrency === "GBP" || sourceCurrency === "CHF"
      ? sourceCurrency
      : "EUR");
  const rate = useMemo(
    () => fxRate(sourceCurrency, targetCurrency),
    [sourceCurrency, targetCurrency],
  );
  const formatNumber = useMemo(
    () => makeFormatter(CURRENCY_GLYPH[targetCurrency]),
    [targetCurrency],
  );

  const columns = useMemo<ColDef<Row>[]>(() => {
    const periodCols: ColDef<Row>[] = statement.periods.map((period) => ({
      colId: `p-${period}`,
      headerName: formatPeriodHeader(period),
      width: 132,
      minWidth: 110,
      sortable: false,
      type: "rightAligned",
      valueGetter: (p: ValueGetterParams<Row>) => {
        const cell = getCell(p.data, period);
        if (!cell || cell.value === null) return null;
        const raw = typeof cell.value === "string" ? Number(cell.value) : cell.value;
        return raw * rate;
      },
      valueFormatter: (p: ValueFormatterParams<Row, number | null>) => {
        // Header rows show no values — just an empty cell.
        if (p.data?.is_header) return "";
        return formatNumber(p.value);
      },
      cellClass: (p) => (getCell(p.data, period)?.is_restated ? "cell-restated" : ""),
      cellStyle: { textAlign: "right" },
      tooltipValueGetter: (p) => {
        // Header rows aren't real data points — no tooltip.
        if (p.data?.is_header) return "";
        const cell = getCell(p.data, period);
        if (!cell) {
          // Cell is empty: the company didn't report this line item for this
          // period. Common reason — accounting standard introduced later
          // (IFRS 15/16 transitions, discontinued-operations splits, etc.)
          return "Not reported by the company in this period";
        }
        const flag = cell.is_restated ? " · restated in a later filing" : "";
        return `Source: FY${cell.source_fiscal_year}${flag}`;
      },
    }));

    return [
      {
        colId: "line",
        headerName: "",
        pinned: "left",
        width: 300,
        minWidth: 220,
        sortable: false,
        valueGetter: (p: ValueGetterParams<Row>) => p.data?.canonical_name ?? "",
        // No cellRenderer — AG Grid renders strings via the default text node.
        // Indent is applied via cellStyle (no React component per cell).
        cellStyle: (p) => {
          const depth = p.data?.depth ?? 0;
          return { paddingLeft: 14 + Math.max(0, depth) * 16 };
        },
      },
      ...periodCols,
    ];
  }, [statement.periods, formatNumber, rate]);

  const rows = useMemo(
    () => [...statement.rows].sort((a, b) => a.display_order - b.display_order),
    [statement.rows],
  );

  return (
    <div
      className={
        "statement-grid " +
        (fillHeight ? "w-full h-full" : "w-full h-[70vh] min-h-[500px]")
      }
      // overscroll-contain stops the horizontal scroll from triggering the
      // browser's swipe-to-go-back gesture on macOS trackpads.
      style={{ overscrollBehaviorX: "contain" }}
    >
      <AgGridReact<Row>
        theme={gridTheme}
        rowData={rows}
        columnDefs={columns}
        getRowClass={(p) => rowClassFor(p.data)}
        suppressCellFocus
        suppressMovableColumns
        suppressDragLeaveHidesColumns
        animateRows={false}
        tooltipShowDelay={300}
        defaultColDef={{
          resizable: false,
          sortable: false,
          suppressHeaderMenuButton: true,
        }}
        getRowId={(p) => `${p.data.display_order}-${p.data.canonical_name}`}
      />
    </div>
  );
}
