import { type DirectoryRow } from "@/components/CompanyDirectory";
import CompanyDirectoryClient from "@/components/CompanyDirectoryClient";
import LogoStack from "@/components/LogoStack";
import PageHeader from "@/components/PageHeader";
import { getCompanyMeta } from "@/lib/companies";
import { listCompanies } from "@/lib/data";
import { loadMarket } from "@/lib/market";

async function loadDirectory(): Promise<DirectoryRow[]> {
  const [companies, market] = await Promise.all([listCompanies(), loadMarket()]);
  const metas = await Promise.all(companies.map((c) => getCompanyMeta(c.slug)));
  return companies.map((c, i) => {
    const meta = metas[i];
    const m = market.get(c.slug);
    return {
      slug: c.slug,
      name: c.name,
      ticker: c.ticker,
      domain: meta?.domain ?? "",
      industry: meta?.industry ?? "—",
      headquarters: meta?.headquarters ?? "—",
      founded: meta?.founded ?? 0,
      logoSrc: meta?.logoSrc ?? "",
      periods: c.periods,
      statements: c.availableStatements,
      hasData: c.hasData,
      price: m?.price ?? null,
      priceCurrency: m?.currency ?? null,
      changePct: m?.change_pct ?? null,
      marketCap: m?.market_cap ?? null,
      sparkline: m?.sparkline ?? [],
    };
  });
}

export default async function HomePage() {
  const rows = await loadDirectory();
  return (
    <div className="flex-1 min-h-0 flex flex-col">
      <PageHeader
        left={
          <>
            <LogoStack rows={rows} />
            <div>
              <h1 className="text-lg font-semibold tracking-tight leading-none">
                Companies
              </h1>
              <p className="text-xs text-muted-foreground mt-1">
                Click a row to open the company.
              </p>
            </div>
          </>
        }
      />
      {/* `flex flex-col` is required so the AG Grid inside actually gets
          height — without it the grid collapses to 0px and looks empty. */}
      <div className="flex-1 min-h-0 flex flex-col">
        <CompanyDirectoryClient rows={rows} />
      </div>
    </div>
  );
}

export const dynamic = "force-static";
