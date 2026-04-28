/** Display-side currency conversion.
 *
 * Rates are illustrative spot averages, NOT real-time. Companies in this
 * dataset all report in EUR; conversion is purely cosmetic so the user can
 * eyeball figures in their preferred currency. Source values stay untouched.
 */

export type DisplayCurrency = "EUR" | "USD" | "GBP" | "CHF" | "CAD";

export const SUPPORTED_CURRENCIES: DisplayCurrency[] = [
  "EUR",
  "USD",
  "GBP",
  "CHF",
  "CAD",
];

export const CURRENCY_GLYPH: Record<DisplayCurrency, string> = {
  EUR: "€",
  USD: "$",
  GBP: "£",
  CHF: "CHF ",
  CAD: "C$",
};

/** Country-flag emoji per supported currency. Shown in the currency picker. */
export const CURRENCY_FLAG: Record<DisplayCurrency, string> = {
  EUR: "🇪🇺",
  USD: "🇺🇸",
  GBP: "🇬🇧",
  CHF: "🇨🇭",
  CAD: "🇨🇦",
};

/** Full label for the currency code — used as a secondary hint in menus. */
export const CURRENCY_LABEL: Record<DisplayCurrency, string> = {
  EUR: "Euro",
  USD: "US Dollar",
  GBP: "British Pound",
  CHF: "Swiss Franc",
  CAD: "Canadian Dollar",
};

// Rates expressed as: 1 unit of `key` = N units of EUR.
// E.g. 1 USD = 0.92 EUR (so 1 EUR = 1.087 USD).
const TO_EUR: Record<DisplayCurrency, number> = {
  EUR: 1.0,
  USD: 0.92,
  GBP: 1.17,
  CHF: 1.04,
  CAD: 0.67,
};

export function fxRate(from: string, to: DisplayCurrency): number {
  const fromUpper = from.toUpperCase();
  if (fromUpper === to) return 1;
  const fromToEur = TO_EUR[fromUpper as DisplayCurrency] ?? 1;
  const toToEur = TO_EUR[to];
  return fromToEur / toToEur;
}
