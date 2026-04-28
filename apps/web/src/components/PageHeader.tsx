import type { ReactNode } from "react";

interface Props {
  /** Left-side content — title, logo, hero, etc. */
  left: ReactNode;
  /** Right-side content — currency selector, etc. Optional. */
  right?: ReactNode;
}

/**
 * Shared page-level header. Same `py-3 border-b` shell on every page so
 * the homepage and the company detail page line up exactly. Left slot
 * gets `min-w-0` so long content (a long company name) truncates instead
 * of pushing the right slot off-screen.
 */
export default function PageHeader({ left, right }: Props) {
  return (
    <div className="px-4 sm:px-6 py-3 border-b flex items-center gap-3 sm:gap-4 flex-wrap">
      <div className="min-w-0 flex-1 flex items-center gap-3 sm:gap-4 flex-wrap">
        {left}
      </div>
      {right && <div className="shrink-0 flex items-center gap-2">{right}</div>}
    </div>
  );
}
