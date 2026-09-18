import { useMemo, useRef, useState } from 'react';

/**
 * Multi-series SVG line chart. Hand-rolled rather than pulled from a
 * chart library: this needs exactly one form, and inheriting the app's
 * existing tokens matters more here than breadth of features.
 *
 * Categorical colours are the validated dark-surface slots, assigned in
 * fixed order and never cycled - a series keeps its colour when the
 * selection changes, so toggling a model off doesn't repaint the rest.
 */
export const SERIES_COLORS = [
  '#3987e5', // blue
  '#d95926', // orange
  '#199e70', // aqua
  '#c98500', // yellow
  '#d55181', // magenta
  '#008300', // green
  '#9085e9', // violet
] as const;

export interface Series {
  label: string;
  values: (number | null)[];
  /** Fixed palette index, so colour follows the entity and not its rank. */
  colorIndex: number;
  /** Rolling averages and other derived lines are drawn dashed. */
  dashed?: boolean;
}

interface LineChartProps {
  labels: string[];
  series: Series[];
  unit: string;
  height?: number;
  /** Roughly how many x labels to print; ticks are sampled to fit. */
  xTickCount?: number;
  formatX?: (label: string) => string;
}

const PAD = { top: 16, right: 16, bottom: 30, left: 48 };

export function LineChart({
  labels,
  series,
  unit,
  height = 240,
  xTickCount = 6,
  formatX = (l) => l,
}: LineChartProps) {
  const [width, setWidth] = useState(720);
  const [hover, setHover] = useState<number | null>(null);
  const wrapRef = useRef<HTMLDivElement | null>(null);

  // Track the container width so the chart is responsive without a
  // dependency on a resize-observer library.
  const measure = (node: HTMLDivElement | null) => {
    wrapRef.current = node;
    if (node) {
      const next = node.getBoundingClientRect().width;
      if (next > 0 && Math.abs(next - width) > 1) setWidth(next);
    }
  };

  const { min, max } = useMemo(() => {
    const all = series.flatMap((s) => s.values).filter((v): v is number => v != null);
    if (!all.length) return { min: 0, max: 1 };
    const lo = Math.min(...all);
    const hi = Math.max(...all);
    if (lo === hi) return { min: lo - 1, max: hi + 1 };
    const pad = (hi - lo) * 0.1;
    return { min: lo - pad, max: hi + pad };
  }, [series]);

  const plotW = Math.max(width - PAD.left - PAD.right, 10);
  const plotH = height - PAD.top - PAD.bottom;
  const n = labels.length;

  const x = (i: number) => PAD.left + (n <= 1 ? plotW / 2 : (i / (n - 1)) * plotW);
  const y = (v: number) => PAD.top + plotH - ((v - min) / (max - min)) * plotH;

  const path = (values: (number | null)[]) => {
    let d = '';
    let pen = false;
    values.forEach((v, i) => {
      if (v == null) {
        pen = false;
        return;
      }
      d += `${pen ? 'L' : 'M'}${x(i).toFixed(1)},${y(v).toFixed(1)} `;
      pen = true;
    });
    return d.trim();
  };

  const yTicks = useMemo(() => {
    const count = 4;
    return Array.from({ length: count + 1 }, (_, i) => min + ((max - min) * i) / count);
  }, [min, max]);

  const xTickIdx = useMemo(() => {
    if (n <= xTickCount) return labels.map((_, i) => i);
    const step = (n - 1) / (xTickCount - 1);
    return Array.from({ length: xTickCount }, (_, i) => Math.round(i * step));
  }, [n, labels, xTickCount]);

  const onMove = (event: React.MouseEvent<SVGSVGElement>) => {
    const rect = event.currentTarget.getBoundingClientRect();
    const px = event.clientX - rect.left;
    if (n <= 1) return setHover(0);
    const i = Math.round(((px - PAD.left) / plotW) * (n - 1));
    setHover(Math.min(n - 1, Math.max(0, i)));
  };

  const decimals = unit === '%' ? 0 : 1;

  return (
    <div ref={measure} className="relative w-full">
      <svg
        width="100%"
        height={height}
        viewBox={`0 0 ${width} ${height}`}
        onMouseMove={onMove}
        onMouseLeave={() => setHover(null)}
        role="img"
        aria-label={`Line chart in ${unit}`}
      >
        {/* Recessive grid - present for reading values, never competing
            with the data marks. */}
        {yTicks.map((t) => (
          <g key={t}>
            <line
              x1={PAD.left}
              x2={width - PAD.right}
              y1={y(t)}
              y2={y(t)}
              stroke="rgba(255,255,255,0.07)"
              strokeWidth={1}
            />
            <text x={PAD.left - 8} y={y(t) + 4} textAnchor="end" className="fill-textMuted" fontSize={10} fontFamily="JetBrains Mono, monospace">
              {t.toFixed(decimals)}
            </text>
          </g>
        ))}

        {xTickIdx.map((i) => (
          <text
            key={i}
            x={x(i)}
            y={height - 10}
            textAnchor={i === 0 ? 'start' : i === n - 1 ? 'end' : 'middle'}
            className="fill-textMuted"
            fontSize={10}
            fontFamily="JetBrains Mono, monospace"
          >
            {formatX(labels[i])}
          </text>
        ))}

        {hover != null && (
          <line
            x1={x(hover)}
            x2={x(hover)}
            y1={PAD.top}
            y2={PAD.top + plotH}
            stroke="rgba(255,255,255,0.25)"
            strokeWidth={1}
          />
        )}

        {series.map((s) => (
          <path
            key={s.label}
            d={path(s.values)}
            fill="none"
            stroke={SERIES_COLORS[s.colorIndex % SERIES_COLORS.length]}
            strokeWidth={2}
            strokeLinecap="round"
            strokeLinejoin="round"
            strokeDasharray={s.dashed ? '5 4' : undefined}
            opacity={s.dashed ? 0.9 : 1}
          />
        ))}

        {/* Hovered points get a surface-coloured ring so overlapping
            series stay distinguishable where they cross. */}
        {hover != null &&
          series.map((s) => {
            const v = s.values[hover];
            if (v == null) return null;
            return (
              <circle
                key={s.label}
                cx={x(hover)}
                cy={y(v)}
                r={4}
                fill={SERIES_COLORS[s.colorIndex % SERIES_COLORS.length]}
                stroke="#0B1220"
                strokeWidth={2}
              />
            );
          })}
      </svg>

      {hover != null && (
        <div
          className="pointer-events-none absolute top-2 rounded-lg border border-white/10 bg-black/85 px-3 py-2 font-mono text-[11px] shadow-xl backdrop-blur-sm"
          style={{
            left: Math.min(Math.max(x(hover) - 70, 0), Math.max(width - 190, 0)),
          }}
        >
          <div className="mb-1 text-textMuted">{formatX(labels[hover])}</div>
          {series.map((s) => {
            const v = s.values[hover];
            return (
              <div key={s.label} className="flex items-center gap-2 whitespace-nowrap">
                <span
                  className="inline-block h-2 w-2 rounded-full"
                  style={{ background: SERIES_COLORS[s.colorIndex % SERIES_COLORS.length] }}
                />
                <span className="text-textMuted">{s.label}</span>
                <span className="ml-auto text-textPrimary">
                  {v == null ? '--' : `${v.toFixed(decimals)} ${unit}`}
                </span>
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}

/** Legend. Always rendered for two or more series, so identity is never
 * carried by colour alone. */
export function Legend({ series }: { series: Series[] }) {
  if (series.length < 2) return null;
  return (
    <div className="flex flex-wrap items-center gap-x-5 gap-y-2 pt-3">
      {series.map((s) => (
        <span key={s.label} className="flex items-center gap-2 font-mono text-[11px] text-textMuted">
          <span
            className="inline-block h-[3px] w-5 rounded-full"
            style={{
              background: s.dashed
                ? `repeating-linear-gradient(90deg, ${SERIES_COLORS[s.colorIndex % SERIES_COLORS.length]} 0 5px, transparent 5px 9px)`
                : SERIES_COLORS[s.colorIndex % SERIES_COLORS.length],
            }}
          />
          {s.label}
        </span>
      ))}
    </div>
  );
}
