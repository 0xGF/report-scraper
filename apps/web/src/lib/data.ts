import "server-only";

import fs from "node:fs/promises";
import path from "node:path";
import { parseIndex, parseStatementDocument } from "./schemas";
import type {
  CompanyEntry,
  CompanySlug,
  IndexFile,
  Statement,
  StatementDocument,
} from "./types";

const EXPORTS_ROOT = path.resolve(process.cwd(), "..", "..", "data", "exports");

export async function loadIndex(): Promise<IndexFile> {
  const raw = await fs.readFile(path.join(EXPORTS_ROOT, "index.json"), "utf8");
  return parseIndex(JSON.parse(raw));
}

export async function loadCompanyMeta(
  slug: CompanySlug,
): Promise<CompanyEntry | null> {
  const idx = await loadIndex();
  return idx.companies.find((c) => c.slug === slug) ?? null;
}

export async function loadStatement(
  slug: CompanySlug,
  statement: Statement,
): Promise<StatementDocument | null> {
  let raw: string;
  try {
    raw = await fs.readFile(
      path.join(EXPORTS_ROOT, slug, `${statement}.json`),
      "utf8",
    );
  } catch (err) {
    if ((err as NodeJS.ErrnoException).code === "ENOENT") return null;
    throw err;
  }
  return parseStatementDocument(JSON.parse(raw));
}

export interface CompanySummary {
  slug: CompanySlug;
  name: string;
  ticker: string;
  availableStatements: Statement[];
  periods: string[];
  /** False for companies that exist in companies.yaml but haven't been
   *  scraped/parsed yet — `availableStatements`/`periods` will be empty. */
  hasData: boolean;
}

export async function listCompanies(): Promise<CompanySummary[]> {
  const { companies, statements } = await loadIndex();
  const byCompany = new Map<
    CompanySlug,
    { availableStatements: Set<Statement>; periods: Set<string> }
  >();
  for (const entry of statements) {
    let rec = byCompany.get(entry.company_slug);
    if (!rec) {
      rec = { availableStatements: new Set<Statement>(), periods: new Set<string>() };
      byCompany.set(entry.company_slug, rec);
    }
    rec.availableStatements.add(entry.statement);
    for (const p of entry.periods) rec.periods.add(p);
  }
  // Iterate `companies` (the canonical directory) so newly-discovered
  // companies without exports still appear, just with empty statements.
  return companies.map((c) => {
    const rec = byCompany.get(c.slug);
    return {
      slug: c.slug,
      name: c.name,
      ticker: c.ticker,
      availableStatements: rec ? [...rec.availableStatements] : [],
      periods: rec ? [...rec.periods].sort().reverse() : [],
      hasData: c.has_data,
    };
  });
}

export type {
  Cell,
  CompanyEntry,
  CompanySlug,
  IndexEntry,
  IndexFile,
  Row,
  Statement,
  StatementDocument,
  Ticker,
} from "./types";
export { STATEMENT_LABELS, asCompanySlug, asTicker } from "./types";
