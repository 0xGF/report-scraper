interface Props {
  values: number[];
  width?: number;
  height?: number;
  /** Stroke width — bumped slightly for the larger hero variant. */
  strokeWidth?: number;
  className?: string;
}

/**
 * Small inline-SVG sparkline. Auto-scales Y to the value range so even a
 * tight intra-day move is visible. Color flips green / red on net direction
 * over the supplied window. Used in the directory grid (compact) and on
 * the company hero (a touch larger).
 */
export default function Sparkline({
  values,
  width = 80,
  height = 22,
  strokeWidth = 1.25,
  className,
}: Props) {
  if (!values || values.length < 2) return null;
  const min = Math.min(...values);
  const max = Math.max(...values);
  const range = max - min || 1;
  const stepX = width / (values.length - 1);
  const points = values
    .map((v, i) => {
      const x = i * stepX;
      const y = height - ((v - min) / range) * height;
      return `${x.toFixed(1)},${y.toFixed(1)}`;
    })
    .join(" ");
  const first = values[0];
  const last = values[values.length - 1];
  const up = (last ?? 0) >= (first ?? 0);
  const stroke = up ? "#16a34a" : "#dc2626";
  const fill = up ? "rgba(22,163,74,0.08)" : "rgba(220,38,38,0.08)";
  return (
    <svg
      width={width}
      height={height}
      viewBox={`0 0 ${width} ${height}`}
      preserveAspectRatio="none"
      className={"block " + (className ?? "")}
      aria-hidden="true"
    >
      <polyline fill={fill} stroke="none" points={`0,${height} ${points} ${width},${height}`} />
      <polyline
        fill="none"
        stroke={stroke}
        strokeWidth={strokeWidth}
        strokeLinejoin="round"
        strokeLinecap="round"
        points={points}
      />
    </svg>
  );
}
