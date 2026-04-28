"use client";

import { ChevronRight } from "lucide-react";
import { Link } from "next-view-transitions";
import { usePathname } from "next/navigation";
import type { CompanyDirEntry } from "@/lib/companies";

interface Props {
  companies: CompanyDirEntry[];
}

/** Desktop-only breadcrumb: "All companies › <Current Company>" when
 * we're on `/c/<slug>`. Hidden on the directory itself. */
export default function CompaniesNav({ companies }: Props) {
  const pathname = usePathname();
  if (pathname === "/") return null;

  const slug = pathname?.startsWith("/c/")
    ? pathname.slice("/c/".length).split("/")[0]
    : null;
  const current = slug ? companies.find((c) => c.slug === slug) : null;

  return (
    <nav
      aria-label="Breadcrumb"
      className="hidden md:flex items-center gap-1 text-xs"
    >
      <Link
        href="/"
        className="inline-flex items-center h-7 px-2 rounded-md text-muted-foreground hover:text-foreground hover:bg-muted/60 transition-colors"
      >
        All companies
      </Link>
      {current && (
        <>
          <ChevronRight
            className="size-3.5 text-muted-foreground/50 shrink-0"
            aria-hidden="true"
          />
          <span
            aria-current="page"
            className="inline-flex items-center h-7 px-2 rounded-md text-foreground font-medium"
          >
            {current.name}
          </span>
        </>
      )}
    </nav>
  );
}
