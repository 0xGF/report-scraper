"use client";

import { Check, ChevronDown } from "lucide-react";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import {
  CURRENCY_FLAG,
  CURRENCY_LABEL,
  SUPPORTED_CURRENCIES,
  type DisplayCurrency,
} from "@/lib/fx";

interface Props {
  value: DisplayCurrency;
  onChange: (next: DisplayCurrency) => void;
  /** e.g. "in millions" or "raw units" */
  unitLabel: string;
}

export default function CurrencySelect({ value, onChange, unitLabel }: Props) {
  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <button
          type="button"
          className="inline-flex items-center gap-1.5 h-7 px-2 -mx-2 rounded-md text-xs hover:bg-muted/60 transition-colors"
        >
          <span className="text-base leading-none" aria-hidden="true">
            {CURRENCY_FLAG[value]}
          </span>
          <span className="font-mono text-foreground">{value}</span>
          <span className="text-muted-foreground">·</span>
          <span className="text-muted-foreground">{unitLabel}</span>
          <ChevronDown className="size-3 text-muted-foreground" />
        </button>
      </DropdownMenuTrigger>
      <DropdownMenuContent align="end" className="min-w-[180px]">
        {SUPPORTED_CURRENCIES.map((c) => (
          <DropdownMenuItem
            key={c}
            onSelect={() => onChange(c)}
            className="text-xs gap-2"
          >
            <span className="text-base leading-none" aria-hidden="true">
              {CURRENCY_FLAG[c]}
            </span>
            <span className="font-mono">{c}</span>
            <span className="text-muted-foreground flex-1 truncate">
              {CURRENCY_LABEL[c]}
            </span>
            {c === value && <Check className="size-3.5 shrink-0" />}
          </DropdownMenuItem>
        ))}
      </DropdownMenuContent>
    </DropdownMenu>
  );
}
