# report-scrape

Take-home pipeline for the Fiscal.ai recruiting process.

**Pipeline stages** (each runnable independently via CLI):

1. `report-scrape scrape <company>` — discover + download IR PDFs (via `report-scrape`)
2. `report-scrape classify <company>` — label each PDF (annual / quarterly / sustainability / other), rules first, LLM fallback
3. `report-scrape parse <company>` — extract Income Statement, Balance Sheet, Cash Flow tables from annual reports
4. `report-scrape normalize <company>` — map raw line-item names to a canonical taxonomy, infer hierarchy
5. `report-scrape build` — consolidate into one "latest restated" view per `(line_item, period)`
6. `report-scrape export` — emit JSON files for the Next.js viewer (`apps/web`)

Companies: **SAP** (Xetra), **ASML** (Amsterdam), **Dassault Systèmes** (Paris).
