import { ArrowLeft, ExternalLink, Globe, MapPin } from "lucide-react";
// `<Link>` from next-view-transitions wraps Next's router with
// `document.startViewTransition`, so navigating BACK to the directory
// also runs the shared-logo animation (plain next/link skips it).
import { Link } from "next-view-transitions";
import AnimatedTitle from "@/components/AnimatedTitle";
import HeroMarket from "@/components/HeroMarket";
import LogoBox from "@/components/LogoBox";
import { Badge } from "@/components/ui/badge";
import { getCompanyMeta } from "@/lib/companies";
import { loadMarket } from "@/lib/market";
import type { StatementDocument } from "@/lib/types";

interface Props {
  doc: StatementDocument;
}

export default async function CompanyHero({ doc }: Props) {
  const [meta, market] = await Promise.all([
    getCompanyMeta(doc.company_slug),
    loadMarket(),
  ]);
  const m = market.get(doc.company_slug);

  return (
    <div className="flex flex-col sm:flex-row sm:items-center gap-3 sm:gap-4">
      {/* Top row: back arrow + logo + name. Stays on one line at any size —
          long names truncate via `min-w-0` + `truncate`. */}
      <div className="flex items-center gap-3 sm:gap-4 min-w-0">
        <Link
          href="/"
          aria-label="All companies"
          className="group size-8 flex items-center justify-center text-muted-foreground hover:text-foreground transition-colors shrink-0"
        >
          <ArrowLeft className="size-4 transition-transform duration-300 ease-out group-hover:-translate-x-0.5" />
        </Link>

        <LogoBox
          src={meta?.logoSrc}
          alt={`${doc.company} logo`}
          size="size-12"
          rounded="rounded-lg"
          fallback={doc.ticker}
          // Same name as the logo cell in CompanyDirectory — pairs the two
          // elements so the browser animates position + scale on navigation.
          style={{ viewTransitionName: `company-logo-${doc.company_slug}` }}
        />

        <div className="min-w-0 flex flex-col gap-1">
          <div className="flex items-center gap-2">
            <h1 className="text-lg font-semibold tracking-tight leading-none truncate">
              <AnimatedTitle text={doc.company} />
            </h1>
            <Badge variant="outline" className="font-mono text-[10px] h-5 shrink-0">
              {doc.ticker} · {doc.exchange}
            </Badge>
          </div>
          {meta && (
            <div className="flex flex-wrap items-center gap-x-4 gap-y-0.5 text-xs text-muted-foreground leading-tight">
              <span className="inline-flex items-center gap-1">
                <MapPin className="size-3" />
                {meta.headquarters}
              </span>
              <span>{meta.industry}</span>
              <a
                href={meta.websiteUrl}
                target="_blank"
                rel="noopener noreferrer"
                className="group inline-flex items-center gap-1 hover:text-foreground transition-colors"
              >
                <Globe className="size-3 transition-transform duration-300 ease-out group-hover:rotate-12" />
                {meta.domain}
                <ExternalLink className="size-2.5 transition-transform duration-300 ease-out group-hover:translate-x-0.5 group-hover:-translate-y-0.5" />
              </a>
              <a
                href={meta.irUrl}
                target="_blank"
                rel="noopener noreferrer"
                className="group inline-flex items-center gap-1 hover:text-foreground transition-colors"
              >
                Investor relations
                <ExternalLink className="size-2.5 transition-transform duration-300 ease-out group-hover:translate-x-0.5 group-hover:-translate-y-0.5" />
              </a>
            </div>
          )}
        </div>
      </div>

      {m && (
        <HeroMarket
          price={m.price}
          marketCap={m.market_cap}
          changePct={m.change_pct}
          sourceCurrency={m.currency}
          sparkline={m.sparkline}
        />
      )}
    </div>
  );
}
