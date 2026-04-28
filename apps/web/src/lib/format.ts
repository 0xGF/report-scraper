/** Display formatters shared across the directory and the company page. */

export const CURRENCY_GLYPH: Record<string, string> = {
  USD: "$",
  EUR: "€",
  GBP: "£",
  CHF: "CHF ",
};

export function glyphFor(currency: string | null | undefined): string {
  return CURRENCY_GLYPH[(currency ?? "").toUpperCase()] ?? "";
}

/** "$1,234.56" / "€1,234.56" — used for live prices. */
export function formatPrice(
  value: number | null | undefined,
  currency: string | null | undefined,
): string {
  if (value === null || value === undefined) return "—";
  const glyph = glyphFor(currency);
  return `${glyph}${value.toLocaleString("en-US", {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  })}`;
}

/** "$1.23T" / "€486.1B" — short-form market cap. */
export function formatMarketCap(
  value: number | null | undefined,
  currency: string | null | undefined,
): string {
  if (value === null || value === undefined) return "—";
  const glyph = glyphFor(currency);
  if (value >= 1e12) return `${glyph}${(value / 1e12).toFixed(2)}T`;
  if (value >= 1e9) return `${glyph}${(value / 1e9).toFixed(1)}B`;
  if (value >= 1e6) return `${glyph}${(value / 1e6).toFixed(0)}M`;
  return `${glyph}${value.toFixed(0)}`;
}

/** Decompose a market cap into the parts an animated counter needs.
 * `{ number: 486.1, decimals: 1, suffix: "B" }` so a count-up component can
 * tween 0 → 486.1 and render the static "B" suffix on the side. */
export function splitMarketCap(
  value: number | null | undefined,
): { number: number; decimals: number; suffix: string } | null {
  if (value === null || value === undefined) return null;
  if (value >= 1e12) return { number: value / 1e12, decimals: 2, suffix: "T" };
  if (value >= 1e9) return { number: value / 1e9, decimals: 1, suffix: "B" };
  if (value >= 1e6) return { number: value / 1e6, decimals: 0, suffix: "M" };
  return { number: value, decimals: 0, suffix: "" };
}
