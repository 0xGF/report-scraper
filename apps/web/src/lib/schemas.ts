/** Runtime schemas for JSON emitted by `report-scrape export`.
 *
 * The Python pipeline owns the canonical shape; these schemas exist so a drift
 * between exporter and viewer fails loudly at load time instead of silently
 * rendering wrong cells.
 */

import { z } from "zod";
import type {
  CompanyEntry,
  CompanySlug,
  IndexEntry,
  IndexFile,
  StatementDocument,
  Ticker,
} from "./types";

const StatementSchema = z.enum(["income", "balance", "cashflow"]);

const CellSchema = z.object({
  value: z.union([z.number(), z.string(), z.null()]),
  source_fiscal_year: z.number().int(),
  is_restated: z.boolean(),
});

const RowSchema = z.object({
  canonical_name: z.string(),
  depth: z.number().int().nonnegative(),
  is_header: z.boolean(),
  is_total: z.boolean(),
  display_order: z.number(),
  parent_name: z.string().nullable(),
  values: z.record(z.string(), CellSchema),
});

const IndexEntrySchema = z.object({
  company_slug: z.string().min(1),
  company_name: z.string(),
  ticker: z.string(),
  statement: StatementSchema,
  periods: z.array(z.string()),
  rows: z.number().int().nonnegative(),
});

const StatementDocumentSchema = z.object({
  company: z.string(),
  company_slug: z.string().min(1),
  ticker: z.string(),
  exchange: z.string(),
  statement: StatementSchema,
  currency: z.string().nullable(),
  unit: z.string().nullable(),
  periods: z.array(z.string()),
  rows: z.array(RowSchema),
});

const CompanyEntrySchema = z.object({
  slug: z.string().min(1),
  name: z.string(),
  ticker: z.string(),
  exchange: z.string(),
  reporting_currency: z.string(),
  ir_url: z.string(),
  website: z.string().nullable(),
  logo_url: z.string().nullable(),
  description: z.string().nullable(),
  sector: z.string().nullable(),
  headquarters: z.string().nullable(),
  founded: z.number().int().nullable(),
  source_note: z.string().nullable(),
  has_data: z.boolean().default(false),
});

const IndexFileSchema = z.object({
  companies: z.array(CompanyEntrySchema),
  statements: z.array(IndexEntrySchema),
});

export function parseIndex(raw: unknown): IndexFile {
  const parsed = IndexFileSchema.parse(raw);
  return {
    companies: parsed.companies.map((c) => ({
      ...c,
      slug: c.slug as CompanySlug,
      ticker: c.ticker as Ticker,
    })) satisfies CompanyEntry[],
    statements: parsed.statements as IndexEntry[],
  };
}

export function parseStatementDocument(raw: unknown): StatementDocument {
  const parsed = StatementDocumentSchema.parse(raw);
  return {
    ...parsed,
    company_slug: parsed.company_slug as CompanySlug,
    ticker: parsed.ticker as Ticker,
  };
}
