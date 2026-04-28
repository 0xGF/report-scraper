import "server-only";

import fs from "node:fs/promises";
import path from "node:path";
import type { CompanySlug } from "./types";

/** Snapshot written by `report-scrape market`. Static JSON — re-run the CLI to refresh. */
export interface MarketRecord {
  slug: CompanySlug;
  ticker: string;
  price: number | null;
  currency: string | null;
  previous_close: number | null;
  change_pct: number | null;
  market_cap: number | null;
  shares_outstanding_m: number | null;
  sparkline: number[];
  fetched_at: number;
}

const MARKET_PATH = path.resolve(process.cwd(), "..", "..", "data", "market.json");

let _cache: { data: Map<CompanySlug, MarketRecord>; mtime: number } | null = null;

/** Cached read — only re-parses if the file's mtime has changed. */
export async function loadMarket(): Promise<Map<CompanySlug, MarketRecord>> {
  let mtime: number;
  try {
    const stat = await fs.stat(MARKET_PATH);
    mtime = stat.mtimeMs;
  } catch {
    return new Map();
  }
  if (_cache && _cache.mtime === mtime) return _cache.data;
  const raw = await fs.readFile(MARKET_PATH, "utf8");
  const records = JSON.parse(raw) as MarketRecord[];
  const data = new Map<CompanySlug, MarketRecord>();
  for (const r of records) data.set(r.slug, r);
  _cache = { data, mtime };
  return data;
}
