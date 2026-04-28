import type { DirectoryRow } from "./CompanyDirectory";

interface Props {
  rows: DirectoryRow[];
  /** How many logos to show in the stack. */
  visible?: number;
}

/** Pick `n` rows pseudo-randomly but deterministically (server render).
 * Deterministic so SSR + hydration agree. Re-runs of the same page get the
 * same picks, which keeps the stack from flickering on refresh. */
function pickStable<T>(items: T[], n: number): T[] {
  if (items.length <= n) return items;
  // Step through `items` with a stride that's coprime-ish with the length;
  // a Set guards against collisions when `items.length` happens to divide
  // the stride evenly (e.g. 4 items × stride 2 → indices 0, 2, 0).
  const seen = new Set<number>();
  const out: T[] = [];
  const start = items.length % 7;
  for (let i = 0; out.length < n && i < items.length * 2; i++) {
    const idx = (start + i * 2) % items.length;
    if (seen.has(idx)) continue;
    seen.add(idx);
    const item = items[idx];
    if (item !== undefined) out.push(item);
  }
  return out;
}

/**
 * Apple-news-style fanned logo cluster.
 *
 *   [ ◐ ][ ◑ ][ ◓ ]  +N more
 *
 * Each logo is a circular avatar with a subtle outward rotation; later avatars
 * sit on top via z-index. Entirely visual — clicks pass through to the parent.
 */
export default function LogoStack({ rows, visible = 3 }: Props) {
  if (rows.length === 0) return null;
  // Only stack rows that have BOTH a real logo path and real pipeline data —
  // newly-discovered companies (no scrape yet) and ones with empty logoSrc
  // would otherwise render as blank/broken cards in the fan.
  const eligible = rows.filter(
    (r) => r.hasData && !!r.logoSrc && r.logoSrc.trim() !== "",
  );
  if (eligible.length === 0) return null;
  const picks = pickStable(eligible, Math.min(visible, eligible.length));

  // Front card straight; each card behind it tilts a bit further right.
  const tilts = [0, 8, 16, 24, 32];

  return (
    <div className="relative flex items-center select-none">
      <div className="flex -space-x-11">
        {picks.map((row, i) => {
          const tilt = tilts[i % tilts.length];
          return (
            <div
              key={row.slug}
              className="relative size-12 aspect-square rounded-md border-2 border-zinc-100 bg-card overflow-hidden shadow-[0_1px_2px_rgba(0,0,0,0.06)]"
              style={{
                transform: `rotate(${tilt}deg)`,
                zIndex: picks.length - i,
              }}
              aria-hidden="true"
            >
              {row.logoSrc ? (
                /* eslint-disable-next-line @next/next/no-img-element */
                <img
                  src={row.logoSrc}
                  alt=""
                  width={48}
                  height={48}
                  // `block` removes the inline baseline gap; the slight scale
                  // crops past the transparent padding some source PNGs ship
                  // with (so all three logos fill their box equally).
                  className="block w-full h-full object-cover scale-[1.25]"
                />
              ) : (
                <div className="size-full grid place-items-center text-xs font-mono text-muted-foreground">
                  {row.ticker.slice(0, 2)}
                </div>
              )}
            </div>
          );
        })}
      </div>
      {/* Count bubble — pinned to the bottom-right of the FRONT card
         (picks[0], which is leftmost in the flex row and highest z). */}
      <div
        className="absolute z-30 grid place-items-center min-w-5 h-5 px-1.5 rounded-full bg-foreground text-background text-[10px] font-semibold tabular-nums ring-1 ring-background"
        style={{
          left: 48,
          top: 40,
          transform: "translate(-50%, -50%)",
        }}
        aria-label={`${rows.length} companies`}
      >
        {rows.length}
      </div>
    </div>
  );
}
