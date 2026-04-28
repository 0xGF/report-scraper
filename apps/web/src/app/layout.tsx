import type { Metadata } from "next";
import { Geist, Geist_Mono } from "next/font/google";
import { cookies } from "next/headers";
import { ViewTransitions } from "next-view-transitions";
import Topbar from "@/components/Topbar";
import { listCompanyMeta } from "@/lib/companies";
import { CURRENCY_COOKIE } from "@/lib/currencyCookie";
import { SUPPORTED_CURRENCIES, type DisplayCurrency } from "@/lib/fx";
import { CurrencyProvider } from "@/lib/useCurrency";
import "./globals.css";

const geistSans = Geist({
  variable: "--font-geist-sans",
  subsets: ["latin"],
});

const geistMono = Geist_Mono({
  variable: "--font-geist-mono",
  subsets: ["latin"],
});

export const metadata: Metadata = {
  title: "European annual-report viewer",
  description:
    "Scraped, parsed, and restatement-consolidated financials for SAP, ASML, and Dassault Systèmes.",
};

export default async function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  const cookieStore = await cookies();
  const companies = await listCompanyMeta();
  const stored = cookieStore.get(CURRENCY_COOKIE)?.value;
  const initialCurrency: DisplayCurrency =
    stored && (SUPPORTED_CURRENCIES as readonly string[]).includes(stored)
      ? (stored as DisplayCurrency)
      : "EUR";
  return (
    <ViewTransitions>
      <html
        lang="en"
        className={`${geistSans.variable} ${geistMono.variable} h-full antialiased`}
      >
        <body className="h-full bg-background text-foreground flex flex-col">
          <CurrencyProvider initial={initialCurrency}>
            <Topbar companies={companies} />
            <main className="flex-1 min-h-0 flex flex-col">{children}</main>
          </CurrencyProvider>
        </body>
      </html>
    </ViewTransitions>
  );
}
