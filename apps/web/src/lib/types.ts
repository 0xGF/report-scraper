/** Shape emitted by `report-scrape export` — safe to import from client components. */

declare const __brand: unique symbol;
type Brand<T, B extends string> = T & { readonly [__brand]: B };

export type CompanySlug = Brand<string, "CompanySlug">;
export type Ticker = Brand<string, "Ticker">;

export const asCompanySlug = (s: string): CompanySlug => s as CompanySlug;
export const asTicker = (s: string): Ticker => s as Ticker;

export type Statement = "income" | "balance" | "cashflow";

export interface Cell {
  value: number | string | null;
  source_fiscal_year: number;
  is_restated: boolean;
}

export interface Row {
  canonical_name: string;
  depth: number;
  is_header: boolean;
  is_total: boolean;
  display_order: number;
  parent_name: string | null;
  values: Record<string, Cell>;
}

export interface StatementDocument {
  company: string;
  company_slug: CompanySlug;
  ticker: Ticker;
  exchange: string;
  statement: Statement;
  currency: string | null;
  unit: string | null;
  periods: string[];
  rows: Row[];
}

export interface IndexEntry {
  company_slug: CompanySlug;
  company_name: string;
  ticker: Ticker;
  statement: Statement;
  periods: string[];
  rows: number;
}

/** Full per-company metadata emitted by `report-scrape export` into `index.json`.
 *  Sourced from `data/companies.yaml` — the viewer reads these fields directly
 *  rather than maintaining a parallel hardcoded list. */
export interface CompanyEntry {
  slug: CompanySlug;
  name: string;
  ticker: Ticker;
  exchange: string;
  reporting_currency: string;
  ir_url: string;
  website: string | null;
  logo_url: string | null;
  description: string | null;
  sector: string | null;
  headquarters: string | null;
  founded: number | null;
  source_note: string | null;
  /** True iff at least one statement was successfully exported.
   *  False for companies that exist in companies.yaml but haven't been
   *  scraped/parsed yet (e.g. just-discovered ones). */
  has_data: boolean;
}

export interface IndexFile {
  companies: CompanyEntry[];
  statements: IndexEntry[];
}

export const STATEMENT_LABELS: Record<Statement, string> = {
  income: "Income Statement",
  balance: "Balance Sheet",
  cashflow: "Cash Flow",
};
