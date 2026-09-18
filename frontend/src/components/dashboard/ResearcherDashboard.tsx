import { useEffect, useState } from 'react';
import { ArrowLeft, MessageSquare, Loader2, AlertCircle, ShieldCheck, FlaskConical } from 'lucide-react';
import {
  fetchCatalog,
  fetchHistoricalTrend,
  fetchModelComparison,
  fetchRegionalContext,
  fetchRegionalNarrative,
  type HistoricalTrend,
  type ModelComparison,
  type RegionalContext,
  type RegionalNarrative,
  type ResearchCatalog,
  type RegionalPoint,
} from '../../lib/research';
import { LineChart, Legend, SERIES_COLORS, type Series } from './LineChart';

interface Props {
  onBackToChat: () => void;
  onHome: () => void;
}

/** Shared panel shell, so every section inherits the same surface, border
 * and typography as the rest of the product rather than a new
 * "dashboard" look. */
function Panel({
  title,
  subtitle,
  children,
}: {
  title: string;
  subtitle?: string;
  children: React.ReactNode;
}) {
  return (
    <section className="rounded-2xl border border-white/10 bg-slate-900/60 p-6 shadow-2xl backdrop-blur-xl">
      <header className="mb-5">
        <h2 className="font-display text-2xl text-textPrimary">{title}</h2>
        {subtitle && <p className="mt-1 font-sans text-sm text-textMuted">{subtitle}</p>}
      </header>
      {children}
    </section>
  );
}

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <label className="flex flex-col gap-1.5">
      <span className="font-mono text-[10px] uppercase tracking-widest text-textMuted">{label}</span>
      {children}
    </label>
  );
}

const inputClass =
  'rounded-lg border border-white/10 bg-black/30 px-3 py-2 font-sans text-sm text-textPrimary ' +
  'placeholder:text-textMuted focus:border-accent/50 focus:outline-none transition-colors';

function ErrorNote({ message }: { message: string }) {
  return (
    <div className="flex items-start gap-2 rounded-xl border border-alertRed bg-alertRed/15 p-3 font-mono text-xs text-alertRed">
      <AlertCircle className="mt-0.5 h-4 w-4 flex-shrink-0" />
      <span>{message}</span>
    </div>
  );
}

function Spinner({ label }: { label: string }) {
  return (
    <div className="flex items-center gap-2 py-8 font-mono text-xs text-textMuted">
      <Loader2 className="h-4 w-4 animate-spin" />
      {label}
    </div>
  );
}

function StatTile({ label, value, unit }: { label: string; value: number | null; unit: string }) {
  return (
    <div className="rounded-xl border border-white/5 bg-black/25 px-4 py-3">
      <div className="font-mono text-[10px] uppercase tracking-widest text-textMuted">{label}</div>
      <div className="mt-1 font-display text-2xl text-textPrimary">
        {value == null ? '--' : value.toFixed(1)}
        <span className="ml-1 font-sans text-sm text-textMuted">{unit}</span>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------
// Feature 1 - multi-model forecast comparison
// ---------------------------------------------------------------------

function ModelComparisonPanel({ catalog }: { catalog: ResearchCatalog }) {
  const [location, setLocation] = useState('Bhopal');
  const [days, setDays] = useState(3);
  const [selected, setSelected] = useState<string[]>(catalog.default);
  const [data, setData] = useState<ModelComparison | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const run = async (loc: string, models: string[], forecastDays: number) => {
    if (!models.length) {
      setError('Select at least one model to compare.');
      return;
    }
    setLoading(true);
    setError(null);
    try {
      setData(await fetchModelComparison(loc, models, forecastDays));
    } catch (err) {
      setError((err as Error).message);
      setData(null);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    void run(location, selected, days);
    // Intentionally on mount only - later runs are triggered by the button.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const toggle = (id: string) =>
    setSelected((prev) => (prev.includes(id) ? prev.filter((m) => m !== id) : [...prev, id]));

  // Palette index is the model's position in the full catalog, not in the
  // current selection, so a model keeps its colour as models are toggled.
  const colorFor = (modelId: string) => catalog.models.findIndex((m) => m.id === modelId);

  return (
    <Panel
      title="Multi-Model Forecast Comparison"
      subtitle="The same location run through several numerical weather models. Where the lines separate, the models disagree."
    >
      <div className="mb-5 flex flex-wrap items-end gap-3">
        <Field label="Location">
          <input
            className={`${inputClass} w-44`}
            value={location}
            onChange={(e) => setLocation(e.target.value)}
            onKeyDown={(e) => e.key === 'Enter' && run(location, selected, days)}
          />
        </Field>
        <Field label="Forecast days">
          <select className={`${inputClass} w-28`} value={days} onChange={(e) => setDays(Number(e.target.value))}>
            {[1, 2, 3, 5, 7, 10, 14].map((d) => (
              <option key={d} value={d} className="bg-slate-900">
                {d} days
              </option>
            ))}
          </select>
        </Field>
        <button
          onClick={() => run(location, selected, days)}
          disabled={loading}
          className="rounded-lg border border-accent/30 bg-accent/10 px-4 py-2 font-mono text-xs uppercase tracking-widest text-accent transition-colors hover:bg-accent/20 disabled:opacity-50"
        >
          Run comparison
        </button>
      </div>

      <div className="mb-6 flex flex-wrap gap-2">
        {catalog.models.map((m, i) => {
          const on = selected.includes(m.id);
          return (
            <button
              key={m.id}
              onClick={() => toggle(m.id)}
              title={m.centre}
              className={`flex items-center gap-2 rounded-full border px-3 py-1.5 font-mono text-[11px] transition-colors ${
                on ? 'border-white/25 bg-white/10 text-textPrimary' : 'border-white/10 text-textMuted hover:bg-white/5'
              }`}
            >
              <span
                className="inline-block h-2 w-2 rounded-full"
                style={{ background: on ? SERIES_COLORS[i % SERIES_COLORS.length] : 'rgba(255,255,255,0.2)' }}
              />
              {m.label}
            </button>
          );
        })}
      </div>

      {error && <ErrorNote message={error} />}
      {loading && <Spinner label="Fetching model runs..." />}

      {data && !loading && (
        <div className="space-y-8">
          {data.parameters.map((p) => {
            const series: Series[] = p.series.map((s) => ({
              label: s.label,
              values: s.values,
              colorIndex: colorFor(s.model),
            }));
            return (
              <div key={p.id}>
                <div className="mb-2 flex items-baseline justify-between">
                  <h3 className="font-sans text-sm text-textPrimary">
                    {p.label} <span className="text-textMuted">({p.unit})</span>
                  </h3>
                  {p.spread != null && (
                    <span className="font-mono text-[11px] text-textMuted">
                      max model spread {p.spread} {p.unit}
                    </span>
                  )}
                </div>
                <LineChart
                  labels={data.time}
                  series={series}
                  unit={p.unit}
                  formatX={(t) => t.slice(5).replace('T', ' ')}
                />
                <Legend series={series} />
              </div>
            );
          })}
        </div>
      )}
    </Panel>
  );
}

// ---------------------------------------------------------------------
// Feature 2 - historical climate trend
// ---------------------------------------------------------------------

function HistoricalTrendPanel({ catalog }: { catalog: ResearchCatalog }) {
  const thisYear = new Date().getFullYear();
  const [location, setLocation] = useState('Bhopal');
  const [parameter, setParameter] = useState('temperature');
  const [startDate, setStartDate] = useState(`${thisYear - 25}-01-01`);
  const [endDate, setEndDate] = useState(`${thisYear - 1}-12-31`);
  const [aggregation, setAggregation] = useState('yearly');
  const [data, setData] = useState<HistoricalTrend | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const run = async () => {
    setLoading(true);
    setError(null);
    try {
      setData(await fetchHistoricalTrend(location, parameter, startDate, endDate, aggregation));
    } catch (err) {
      setError((err as Error).message);
      setData(null);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    void run();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const series: Series[] = data
    ? [
        { label: data.label, values: data.values, colorIndex: 0 },
        {
          label: `${data.rolling_window}-point rolling average`,
          values: data.rolling_average,
          colorIndex: 3,
          dashed: true,
        },
      ]
    : [];

  return (
    <Panel
      title="Historical Climate Trend"
      subtitle="Observed past data from the Open-Meteo archive (back to 1940). No model, no LLM - statistics are computed server-side."
    >
      <div className="mb-5 flex flex-wrap items-end gap-3">
        <Field label="Location">
          <input className={`${inputClass} w-40`} value={location} onChange={(e) => setLocation(e.target.value)} />
        </Field>
        <Field label="Parameter">
          <select className={`${inputClass} w-40`} value={parameter} onChange={(e) => setParameter(e.target.value)}>
            {catalog.historical_parameters.map((p) => (
              <option key={p.id} value={p.id} className="bg-slate-900">
                {p.label}
              </option>
            ))}
          </select>
        </Field>
        <Field label="From">
          <input type="date" className={`${inputClass} w-40`} value={startDate} onChange={(e) => setStartDate(e.target.value)} />
        </Field>
        <Field label="To">
          <input type="date" className={`${inputClass} w-40`} value={endDate} onChange={(e) => setEndDate(e.target.value)} />
        </Field>
        <Field label="Aggregate">
          <select className={`${inputClass} w-32`} value={aggregation} onChange={(e) => setAggregation(e.target.value)}>
            {['daily', 'monthly', 'yearly'].map((a) => (
              <option key={a} value={a} className="bg-slate-900">
                {a}
              </option>
            ))}
          </select>
        </Field>
        <button
          onClick={run}
          disabled={loading}
          className="rounded-lg border border-accent/30 bg-accent/10 px-4 py-2 font-mono text-xs uppercase tracking-widest text-accent transition-colors hover:bg-accent/20 disabled:opacity-50"
        >
          Load trend
        </button>
      </div>

      {error && <ErrorNote message={error} />}
      {loading && <Spinner label="Querying the archive..." />}

      {data && !loading && (
        <>
          <div className="mb-5 grid grid-cols-2 gap-3 sm:grid-cols-4">
            <StatTile label="Mean" value={data.stats.mean} unit={data.unit} />
            <StatTile label="Minimum" value={data.stats.min} unit={data.unit} />
            <StatTile label="Maximum" value={data.stats.max} unit={data.unit} />
            <div className="rounded-xl border border-white/5 bg-black/25 px-4 py-3">
              <div className="font-mono text-[10px] uppercase tracking-widest text-textMuted">Observations</div>
              <div className="mt-1 font-display text-2xl text-textPrimary">{data.observations.toLocaleString()}</div>
            </div>
          </div>
          <LineChart labels={data.labels} series={series} unit={data.unit} height={280} />
          <Legend series={series} />
          <p className="pt-3 font-mono text-[11px] text-textMuted">
            {data.location} · {data.start_date} to {data.end_date} · {data.aggregation} ·{' '}
            {data.stats.count} points plotted
          </p>
        </>
      )}
    </Panel>
  );
}

// ---------------------------------------------------------------------
// Feature 3 - regional context
// ---------------------------------------------------------------------

function num(value: unknown, digits = 1): string {
  return typeof value === 'number' ? value.toFixed(digits) : '--';
}

function PointRow({ point }: { point: RegionalPoint }) {
  const c = point.current;
  return (
    <tr className="border-t border-white/5">
      <td className="py-2 pr-3 font-sans text-textPrimary">{point.name}</td>
      <td className="py-2 pr-3 text-right font-mono text-textMuted">{num(c.temperature_2m)}</td>
      <td className="py-2 pr-3 text-right font-mono text-textMuted">{num(c.wind_speed_10m)}</td>
      <td className="py-2 pr-3 text-right font-mono text-textMuted">{num(c.wind_gusts_10m)}</td>
      <td className="py-2 text-right font-mono text-textPrimary">{num(c.pressure_msl)}</td>
    </tr>
  );
}

function RegionTable({ title, points }: { title: string; points: RegionalPoint[] }) {
  return (
    <div>
      <h3 className="mb-2 font-mono text-[10px] uppercase tracking-widest text-textMuted">{title}</h3>
      <table className="w-full text-xs">
        <thead>
          <tr className="font-mono text-[10px] uppercase tracking-wider text-textMuted">
            <th className="pb-1 text-left font-normal">Point</th>
            <th className="pb-1 text-right font-normal">°C</th>
            <th className="pb-1 text-right font-normal">Wind</th>
            <th className="pb-1 text-right font-normal">Gust</th>
            <th className="pb-1 text-right font-normal">hPa</th>
          </tr>
        </thead>
        <tbody>
          {points.map((p) => (
            <PointRow key={p.name} point={p} />
          ))}
        </tbody>
      </table>
    </div>
  );
}

function RegionalContextPanel() {
  const [data, setData] = useState<RegionalContext | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [narrative, setNarrative] = useState<RegionalNarrative | null>(null);
  const [narrativeLoading, setNarrativeLoading] = useState(false);

  useEffect(() => {
    fetchRegionalContext()
      .then(setData)
      .catch((err) => setError((err as Error).message));
  }, []);

  const loadNarrative = async () => {
    setNarrativeLoading(true);
    try {
      setNarrative(await fetchRegionalNarrative());
    } catch (err) {
      setNarrative({ narrative: null, verified: false, error: (err as Error).message, latency_ms: 0 });
    } finally {
      setNarrativeLoading(false);
    }
  };

  const seaBy = (region: string) => (data?.sea_points ?? []).filter((p) => p.region === region);
  const coastBy = (region: string) => (data?.coastal_points ?? []).filter((p) => p.region === region);

  return (
    <Panel
      title="Regional Weather-System Context"
      subtitle="Arabian Sea and Bay of Bengal sampling points alongside the coastal cities on each side. Data for comparison - the dashboard asserts no causal link between them."
    >
      {error && <ErrorNote message={error} />}
      {!data && !error && <Spinner label="Sampling 12 regional points..." />}

      {data && (
        <>
          <div className="mb-6 grid grid-cols-1 gap-3 sm:grid-cols-2">
            <div className="rounded-xl border border-white/5 bg-black/25 px-4 py-3">
              <div className="font-mono text-[10px] uppercase tracking-widest text-textMuted">
                Lowest sea-level pressure
              </div>
              <div className="mt-1 font-display text-xl text-textPrimary">
                {data.lowest_pressure ? `${data.lowest_pressure.value} hPa` : '--'}
              </div>
              <div className="font-sans text-xs text-textMuted">{data.lowest_pressure?.name}</div>
            </div>
            <div className="rounded-xl border border-white/5 bg-black/25 px-4 py-3">
              <div className="font-mono text-[10px] uppercase tracking-widest text-textMuted">Strongest gust at sea</div>
              <div className="mt-1 font-display text-xl text-textPrimary">
                {data.strongest_gust ? `${data.strongest_gust.value} m/s` : '--'}
              </div>
              <div className="font-sans text-xs text-textMuted">{data.strongest_gust?.name}</div>
            </div>
          </div>

          <div className="grid grid-cols-1 gap-8 lg:grid-cols-2">
            <div className="space-y-6">
              <RegionTable title="Arabian Sea" points={seaBy('arabian_sea')} />
              <RegionTable title="West coast" points={coastBy('west_coast')} />
            </div>
            <div className="space-y-6">
              <RegionTable title="Bay of Bengal" points={seaBy('bay_of_bengal')} />
              <RegionTable title="East coast" points={coastBy('east_coast')} />
            </div>
          </div>

          <div className="mt-6 border-t border-white/5 pt-5">
            {!narrative && (
              <button
                onClick={loadNarrative}
                disabled={narrativeLoading}
                className="rounded-lg border border-white/10 px-4 py-2 font-mono text-xs uppercase tracking-widest text-textMuted transition-colors hover:border-accent/30 hover:text-accent disabled:opacity-50"
              >
                {narrativeLoading ? 'Generating and verifying...' : 'Generate grounded description'}
              </button>
            )}
            {narrativeLoading && <Spinner label="Generating, then verifying against the fetched data..." />}

            {narrative?.narrative && (
              <div className="space-y-3">
                <div className="flex items-center gap-2 font-mono text-[11px] uppercase text-emerald-400">
                  <ShieldCheck className="h-3.5 w-3.5" />
                  Verified against fetched data
                  <span className="ml-auto text-textMuted">{narrative.latency_ms}ms</span>
                </div>
                <p className="font-sans text-sm leading-relaxed text-textPrimary">{narrative.narrative}</p>
                <p className="font-mono text-[11px] text-textMuted">{narrative.disclaimer}</p>
              </div>
            )}
            {narrative && !narrative.narrative && <ErrorNote message={narrative.error ?? 'No description available.'} />}
          </div>
        </>
      )}
    </Panel>
  );
}

// ---------------------------------------------------------------------
// Explicitly out of scope - labelled as a research direction, not a
// feature. No mocked output, because a fake storm track is worse than
// no storm track.
// ---------------------------------------------------------------------

function FutureWorkCard() {
  return (
    <section className="rounded-2xl border border-dashed border-white/15 bg-slate-900/30 p-6">
      <div className="mb-2 flex items-center gap-2">
        <FlaskConical className="h-4 w-4 text-textMuted" />
        <span className="font-mono text-[10px] uppercase tracking-widest text-textMuted">Future research direction</span>
      </div>
      <h2 className="font-display text-xl text-textPrimary">GNN-based cloud and storm-system tracking</h2>
      <p className="mt-2 max-w-3xl font-sans text-sm leading-relaxed text-textMuted">
        Applying a graph neural network to satellite image time-series for system-motion prediction is a separate
        machine-learning research effort, not a dashboard feature. It is not implemented, and nothing on this page
        simulates it - no placeholder tracks, no synthetic output.
      </p>
    </section>
  );
}

// ---------------------------------------------------------------------

export function ResearcherDashboard({ onBackToChat, onHome }: Props) {
  const [catalog, setCatalog] = useState<ResearchCatalog | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    fetchCatalog()
      .then(setCatalog)
      .catch((err) => setError((err as Error).message));
  }, []);

  return (
    <div className="relative z-10 min-h-screen w-full bg-background/80 px-4 py-10 backdrop-blur-sm sm:px-8">
      <div className="mx-auto max-w-6xl space-y-6">
        <header className="flex flex-wrap items-center gap-4 border-b border-white/10 pb-6">
          <button
            onClick={onHome}
            className="rounded-lg p-2 text-textMuted transition-colors hover:bg-white/5 hover:text-textPrimary"
            title="Back to home"
          >
            <ArrowLeft className="h-5 w-5" />
          </button>
          <div>
            <h1 className="font-display text-3xl text-textPrimary">Researcher Dashboard</h1>
            <p className="font-sans text-sm text-textMuted">
              Forecast divergence, climate history, and regional context - all grounded in fetched data.
            </p>
          </div>
          <button
            onClick={onBackToChat}
            className="ml-auto flex items-center gap-2 rounded-lg border border-accent/30 bg-accent/10 px-4 py-2 font-mono text-xs uppercase tracking-widest text-accent transition-colors hover:bg-accent/20"
          >
            <MessageSquare className="h-4 w-4" />
            Ask a question
          </button>
        </header>

        {error && <ErrorNote message={error} />}
        {!catalog && !error && <Spinner label="Loading model catalog..." />}

        {catalog && (
          <>
            <ModelComparisonPanel catalog={catalog} />
            <HistoricalTrendPanel catalog={catalog} />
            <RegionalContextPanel />
            <FutureWorkCard />
          </>
        )}
      </div>
    </div>
  );
}
