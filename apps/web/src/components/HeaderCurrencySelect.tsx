"use client";

import CurrencySelect from "./CurrencySelect";
import { useCurrency } from "@/lib/useCurrency";

/** Thin client wrapper that connects the header's currency dropdown to the
 * shared `useCurrency` hook (localStorage-backed). Lets server-rendered
 * pages mount the selector without lifting the state up themselves. */
export default function HeaderCurrencySelect() {
  const [value, setValue] = useCurrency();
  // Header is page-level; no specific statement context, so we render the
  // bare currency dropdown without the per-statement unit hint.
  return <CurrencySelect value={value} onChange={setValue} unitLabel="display" />;
}
