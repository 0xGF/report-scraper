"use client";

import { Building2, Search } from "lucide-react";
import { Link } from "next-view-transitions";
import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import {
  Command,
  CommandEmpty,
  CommandGroup,
  CommandInput,
  CommandItem,
  CommandList,
} from "@/components/ui/command";
import CompaniesNav from "@/components/CompaniesNav";
import {
  Popover,
  PopoverContent,
  PopoverTrigger,
} from "@/components/ui/popover";
import type { CompanyDirEntry } from "@/lib/companies";

interface Props {
  companies: CompanyDirEntry[];
}

export default function Topbar({ companies }: Props) {
  const router = useRouter();
  const [open, setOpen] = useState(false);

  // Cmd+K / Ctrl+K → focus search
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "k") {
        e.preventDefault();
        setOpen((v) => !v);
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  const go = (slug: string) => {
    setOpen(false);
    router.push(`/c/${slug}`);
  };

  return (
    <header className="sticky top-0 z-30 w-full border-b bg-background/80 backdrop-blur-sm">
      <div className="flex items-center gap-3 sm:gap-4 px-4 sm:px-6 h-13 w-full">
        <Link
          href="/"
          className="group inline-flex items-center gap-2 text-sm font-medium tracking-tight shrink-0"
        >
          <Building2 className="size-4 transition-transform duration-300 ease-out group-hover:-rotate-6 group-hover:scale-110" />
          <span>Annual reports</span>
        </Link>

        {/* Desktop-only breadcrumb: All companies › <Current> on detail pages. */}
        <CompaniesNav companies={companies} />

        <Popover open={open} onOpenChange={setOpen}>
          <PopoverTrigger asChild>
            <button
              type="button"
              aria-label="Search companies"
              // Mobile: 32×32 icon-only button. ≥sm: full search bar with
              // placeholder text + ⌘K hint, capped to max-w-sm on the right.
              className="group ml-auto flex items-center gap-2 size-7 sm:h-8 sm:size-auto sm:w-full sm:max-w-sm sm:px-2.5 justify-center sm:justify-start rounded-md border bg-background text-xs text-muted-foreground hover:text-foreground hover:bg-muted/60 transition-colors"
            >
              <Search className="size-3.5 transition-transform duration-300 ease-out group-hover:scale-110 group-hover:rotate-6" />
              <span className="hidden sm:inline flex-1 text-left">
                Search companies…
              </span>
              <kbd className="hidden sm:inline-flex items-center gap-0.5 font-mono text-[10px] text-muted-foreground/80">
                ⌘K
              </kbd>
            </button>
          </PopoverTrigger>
          <PopoverContent
            align="end"
            sideOffset={8}
            // Mobile: span the full screen width minus the page's 16px padding
            // each side (32px total). ≥sm: match trigger width, capped sensibly.
            className="p-0 w-[calc(100vw-2rem)] sm:w-[var(--radix-popover-trigger-width)] sm:min-w-[320px] sm:max-w-md"
          >
            <Command>
              <CommandInput placeholder="Search companies, tickers, industries…" />
              <CommandList>
                <CommandEmpty>No companies match.</CommandEmpty>
                <CommandGroup heading="Companies">
                  {companies.map((c) => (
                    <CommandItem
                      key={c.slug}
                      value={`${c.name} ${c.slug} ${c.industry} ${c.headquarters}`}
                      onSelect={() => go(c.slug)}
                      className="gap-3"
                    >
                      <div className="size-5 aspect-square rounded border-2 border-zinc-100 bg-card overflow-hidden shrink-0">
                        {/* eslint-disable-next-line @next/next/no-img-element */}
                        <img
                          src={c.logoSrc}
                          alt=""
                          width={20}
                          height={20}
                          className="size-full object-cover"
                        />
                      </div>
                      <div className="flex flex-col min-w-0">
                        <span className="truncate">{c.name}</span>
                        <span className="text-[11px] text-muted-foreground truncate">
                          {c.industry} · {c.headquarters}
                        </span>
                      </div>
                    </CommandItem>
                  ))}
                </CommandGroup>
              </CommandList>
            </Command>
          </PopoverContent>
        </Popover>

      </div>
    </header>
  );
}
