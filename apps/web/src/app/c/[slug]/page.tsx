import { notFound } from "next/navigation";
import CompanyHero from "@/components/CompanyHero";
import PageHeader from "@/components/PageHeader";
import StatementTabs from "@/components/StatementTabs";
import {
  asCompanySlug,
  loadStatement,
  type Statement,
  type StatementDocument,
} from "@/lib/data";

interface PageProps {
  params: Promise<{ slug: string }>;
}

const KINDS: Statement[] = ["income", "balance", "cashflow"];

export default async function CompanyPage({ params }: PageProps) {
  const { slug } = await params;
  const companySlug = asCompanySlug(slug);

  const docs = await Promise.all(KINDS.map((k) => loadStatement(companySlug, k)));
  const statements = KINDS.reduce<Partial<Record<Statement, StatementDocument>>>(
    (acc, kind, i) => {
      const doc = docs[i];
      if (doc) acc[kind] = doc;
      return acc;
    },
    {},
  );

  const first = Object.values(statements)[0];
  if (!first) notFound();

  return (
    <div className="flex-1 min-h-0 flex flex-col">
      <PageHeader left={<CompanyHero doc={first} />} />
      <div className="flex-1 min-h-0 flex flex-col">
        <StatementTabs statements={statements} fillHeight />
      </div>
    </div>
  );
}
