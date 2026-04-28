/** Shared cookie name for the user's display-currency preference. Kept in
 * its own module so both the server (layout.tsx, reading via `cookies()`)
 * and the client (`CurrencyProvider`, writing via `document.cookie`) can
 * import it without crossing the server/client boundary. */
export const CURRENCY_COOKIE = "report-scrape-currency";
