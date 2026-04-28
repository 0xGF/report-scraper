"use client";

import CountUp from "./CountUp";
import Sparkline from "./Sparkline";
import { glyphFor, splitMarketCap } from "@/lib/format";
import { fxRate } from "@/lib/fx";
import { useCurrency } from "@/lib/useCurrency";

interface Props {
  /** Raw price in `sourceCurrency`. */
  price: number | null;
  marketCap: number | null;
  changePct: number | null;
  sourceCurrency: string | null;
  sparkline: number[];
}

/**
 * Client island for the company hero's market block. Lives in a client
 * component so it can read the localStorage-backed display currency and
 * convert the source values on the fly.
 */
export default function HeroMarket({
  price,
  marketCap,
  changePct,
  sourceCurrency,
  sparkline,
}: Props) {
  const [displayCurrency] = useCurrency();
  const rate = fxRate(sourceCurrency ?? "EUR", displayCurrency);
  const glyph = glyphFor(displayCurrency);
  const convertedPrice = price == null ? null : price * rate;
  const convertedMcap = marketCap == null ? null : marketCap * rate;
  const mc = splitMarketCap(convertedMcap);

  return (
    <div className="flex items-start justify-center sm:justify-start gap-8 sm:gap-12 sm:ml-4 sm:pl-8 sm:border-l border-border shrink-0 w-full sm:w-auto pt-2 sm:pt-0 border-t sm:border-t-0">
      {convertedPrice != null && (
        <PriceColumn
          label="Price"
          value={convertedPrice}
          decimals={2}
          prefix={glyph}
          changePct={changePct}
        />
      )}
      {mc && (
        <PriceColumn
          label="Market cap"
          value={mc.number}
          decimals={mc.decimals}
          prefix={glyph}
          suffix={mc.suffix}
          changePct={changePct}
        />
      )}
      {sparkline.length >= 2 && (
        <div className="flex flex-col items-end gap-0.5">
          <span className="text-[10px] uppercase tracking-wide text-muted-foreground">
            3 mo
          </span>
          <Sparkline values={sparkline} width={120} height={30} strokeWidth={1.5} />
        </div>
      )}
    </div>
  );
}

interface PriceColumnProps {
  label: string;
  value: number;
  decimals: number;
  prefix?: string;
  suffix?: string;
  changePct: number | null;
}

function PriceColumn({
  label,
  value,
  decimals,
  prefix = "",
  suffix = "",
  changePct,
}: PriceColumnProps) {
  return (
    <div className="flex flex-col items-end gap-0.5 leading-tight">
      <span className="text-[10px] uppercase tracking-wide text-muted-foreground">
        {label}
      </span>
      <span className="font-medium">
        <CountUp value={value} decimals={decimals} prefix={prefix} suffix={suffix} />
      </span>
      {changePct != null && (
        <span
          className={
            "text-[10px] tabular-nums " +
            (changePct >= 0 ? "text-green-600" : "text-red-600")
          }
        >
          {changePct >= 0 ? "+" : ""}
          {changePct.toFixed(2)}%
        </span>
      )}
    </div>
  );
}
