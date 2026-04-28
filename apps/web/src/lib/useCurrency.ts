"use client";

import {
  createContext,
  createElement,
  useCallback,
  useContext,
  useState,
  type ReactNode,
} from "react";
import { CURRENCY_COOKIE } from "./currencyCookie";
import { SUPPORTED_CURRENCIES, type DisplayCurrency } from "./fx";

/** Display-currency preference, persisted to a cookie so the SERVER can
 * read it during SSR and render the right values on first paint — no
 * post-hydration shift in the price/market-cap section. */

const DEFAULT: DisplayCurrency = "EUR";

function isSupportedCurrency(v: unknown): v is DisplayCurrency {
  return (
    typeof v === "string" &&
    (SUPPORTED_CURRENCIES as readonly string[]).includes(v)
  );
}

type Ctx = readonly [DisplayCurrency, (c: DisplayCurrency) => void];

const CurrencyContext = createContext<Ctx>([DEFAULT, () => {}]);

export function CurrencyProvider({
  initial,
  children,
}: {
  initial: DisplayCurrency;
  children: ReactNode;
}) {
  const [value, setValue] = useState<DisplayCurrency>(
    isSupportedCurrency(initial) ? initial : DEFAULT,
  );

  const set = useCallback((next: DisplayCurrency) => {
    setValue(next);
    if (typeof document !== "undefined") {
      const oneYear = 60 * 60 * 24 * 365;
      document.cookie = `${CURRENCY_COOKIE}=${next}; path=/; max-age=${oneYear}; SameSite=Lax`;
    }
  }, []);

  return createElement(
    CurrencyContext.Provider,
    { value: [value, set] as const },
    children,
  );
}

export function useCurrency(): Ctx {
  return useContext(CurrencyContext);
}
