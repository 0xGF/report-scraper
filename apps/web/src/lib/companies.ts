/** Per-company display metadata.
 *
 * The single source of truth is `data/companies.yaml`, which the Python
 * exporter inlines into `data/exports/index.json`. This module reshapes the
 * raw JSON into the small interface the viewer's components want — derived
 * fields like `domain` (extracted from the website URL) and a `logoSrc`
 * fallback live here so the components stay simple.
 *
 * Server-only: depends on `loadIndex` which reads from disk.
 */

import "server-only";

import fs from "node:fs";
import path from "node:path";
import { loadIndex } from "./data";
import type { CompanyEntry } from "./types";

// Probe a few common extensions and cache the first that exists per slug.
// SVG wins over PNG when both are present — they scale crisper into the
// rounded box at every size.
const _LOGO_EXTS = ["svg", "png"] as const;
const _logoPathCache = new Map<string, string | null>();
function localLogoPath(slug: string): string | null {
  if (_logoPathCache.has(slug)) return _logoPathCache.get(slug) ?? null;
  for (const ext of _LOGO_EXTS) {
    const abs = path.resolve(process.cwd(), "public", "logos", `${slug}.${ext}`);
    if (fs.existsSync(abs)) {
      const rel = `/logos/${slug}.${ext}`;
      _logoPathCache.set(slug, rel);
      return rel;
    }
  }
  _logoPathCache.set(slug, null);
  return null;
}

export interface CompanyMeta {
  domain: string;
  logoSrc: string;
  irUrl: string;
  websiteUrl: string;
  headquarters: string;
  founded: number;
  industry: string;
  blurb: string;
  sourceNote: string;
}

export interface CompanyDirEntry extends CompanyMeta {
  slug: string;
  name: string;
}

function safeHostname(url: string | null): string {
  if (!url) return "";
  try {
    return new URL(url).hostname.replace(/^www\./, "");
  } catch {
    return "";
  }
}

function metaFromEntry(c: CompanyEntry): CompanyMeta {
  // Logo precedence:
  //   1. A locally-cached file under /public/logos/<slug>.{svg,png} — most
  //      reliable, no network. SVG wins over PNG when both are present.
  //   2. The discovery agent's `logo_url` from the YAML (remote URL).
  //   3. "" — components fall back to ticker initials in a placeholder.
  let logoSrc = localLogoPath(c.slug) ?? "";
  if (!logoSrc && c.logo_url) logoSrc = c.logo_url;
  return {
    domain: safeHostname(c.website),
    logoSrc,
    irUrl: c.ir_url,
    websiteUrl: c.website ?? "",
    headquarters: c.headquarters ?? "",
    founded: c.founded ?? 0,
    industry: c.sector ?? "",
    blurb: c.description ?? "",
    sourceNote: c.source_note ?? "",
  };
}

export async function getCompanyMeta(slug: string): Promise<CompanyMeta | null> {
  const idx = await loadIndex();
  const entry = idx.companies.find((c) => c.slug === slug);
  return entry ? metaFromEntry(entry) : null;
}

export async function listCompanyMeta(): Promise<CompanyDirEntry[]> {
  const idx = await loadIndex();
  return idx.companies.map((c) => ({
    ...metaFromEntry(c),
    slug: c.slug,
    name: c.name,
  }));
}
