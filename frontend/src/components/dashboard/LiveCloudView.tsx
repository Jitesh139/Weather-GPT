import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import L from 'leaflet';
import 'leaflet/dist/leaflet.css';
import { AlertCircle, Loader2, Pause, Play, Satellite } from 'lucide-react';
import { fetchMapCities, type MapCity } from '../../lib/research';

/**
 * Live Cloud View - NASA GIBS satellite imagery over a Leaflet map.
 *
 * Layer identifiers, tile matrix sets, formats and zoom ceilings below were
 * read out of the live WMTS capabilities document
 * (gibs.earthdata.nasa.gov/wmts/epsg3857/best/1.0.0/WMTSCapabilities.xml)
 * and each was then probed with real tile requests. Do not edit them from
 * memory: the matrix set encodes the layer's maximum native zoom, and GIBS
 * answers anything above it with an HTML/XML error body rather than an
 * image, which a browser renders as a broken tile.
 *
 * GIBS REST tiles are ordered {TileMatrix}/{TileRow}/{TileCol} = {z}/{y}/{x},
 * not Leaflet's default {z}/{x}/{y}.
 *
 * GIBS serves tiles with `Cache-Control: no-store`, so nothing it returns is
 * ever reused from the browser cache. That is why frames are pooled (current
 * plus the next two) instead of all being mounted at once - with every frame
 * on the map, a single zoom re-requests hundreds of uncacheable tiles.
 */

const GIBS_BASE = 'https://gibs.earthdata.nasa.gov/wmts/epsg3857/best';

/** 1x1 transparent PNG. Any tile that fails resolves to this, so a broken
 *  image icon or a provider's error artwork is never visible. */
const BLANK_TILE =
  'data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNkYPhfDwAChwGA60e6kgAAAABJRU5ErkJggg==';

// Keyless basemap. CARTO's free basemap endpoint needs no token and was
// verified returning clean PNGs at every zoom this map allows.
const BASEMAP_URL = 'https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png';
const BASEMAP_ATTRIBUTION =
  '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors &copy; <a href="https://carto.com/attributions">CARTO</a> | Imagery: NASA EOSDIS GIBS';

const MIN_ZOOM = 3;
const MAX_ZOOM = 9;
// India plus the surrounding ocean. Keeps the viewport inside the region the
// geostationary disk actually covers.
const MAX_BOUNDS = L.latLngBounds([-15, 35], [50, 125]);

type Cadence = 'geostationary' | 'daily';

interface CloudLayer {
  id: string;
  label: string;
  layer: string;
  matrixSet: string;
  ext: 'png' | 'jpeg';
  /** Highest zoom the matrix set publishes; above this Leaflet upscales. */
  maxNativeZoom: number;
  cadence: Cadence;
  caveat?: string;
}

const CLOUD_LAYERS: CloudLayer[] = [
  {
    id: 'himawari-b13',
    label: 'Himawari - Clean Infrared (Band 13)',
    layer: 'Himawari_AHI_Band13_Clean_Infrared',
    matrixSet: 'GoogleMapsCompatible_Level6',
    ext: 'png',
    maxNativeZoom: 6,
    cadence: 'geostationary',
  },
  {
    id: 'goes-east-geocolor',
    label: 'GOES-East - GeoColor (no India coverage)',
    layer: 'GOES-East_ABI_GeoColor',
    matrixSet: 'GoogleMapsCompatible_Level7',
    ext: 'png',
    maxNativeZoom: 7,
    cadence: 'geostationary',
    caveat:
      'GOES-East sits over the Americas; its disk does not reach India, so this layer is blank at this view. Included for completeness, not for use here.',
  },
  {
    id: 'viirs-truecolor',
    label: 'VIIRS - True Color (daily fallback)',
    layer: 'VIIRS_SNPP_CorrectedReflectance_TrueColor',
    matrixSet: 'GoogleMapsCompatible_Level9',
    ext: 'jpeg',
    maxNativeZoom: 9,
    cadence: 'daily',
    caveat: 'One polar-orbiter composite per day - a daily still, not cloud motion.',
  },
];

const STEP_MINUTES = 10;
const GEO_FRAMES = 36; // 6 hours
const GEO_LAG_MINUTES = 40;
const DAILY_FRAMES = 7;

const FRAME_TIMEOUT_MS = 6000;
const TILE_RETRY_MS = 1000;
const PLAYBACK_MS = 550;
const RESUME_AFTER_MS = 500;
const SPINNER_DELAY_MS = 150;
const SLOW_AFTER_MS = 8000;
const PRELOAD_AHEAD = 2;
const FAILURE_LIMIT = 3;

interface Frame {
  time: string;
  at: Date;
}

function buildFrames(cfg: CloudLayer): Frame[] {
  const now = Date.now();
  if (cfg.cadence === 'daily') {
    const out: Frame[] = [];
    for (let i = DAILY_FRAMES; i >= 1; i--) {
      const at = new Date(now - i * 86_400_000);
      out.push({ time: at.toISOString().slice(0, 10), at });
    }
    return out;
  }
  const stepMs = STEP_MINUTES * 60_000;
  const newest = Math.floor((now - GEO_LAG_MINUTES * 60_000) / stepMs) * stepMs;
  const out: Frame[] = [];
  for (let i = GEO_FRAMES - 1; i >= 0; i--) {
    const at = new Date(newest - i * stepMs);
    out.push({ time: at.toISOString().replace('.000Z', 'Z'), at });
  }
  return out;
}

function tileUrl(cfg: CloudLayer, time: string): string {
  return `${GIBS_BASE}/${cfg.layer}/default/${time}/${cfg.matrixSet}/{z}/{y}/{x}.${cfg.ext}`;
}

const IST_TIME = new Intl.DateTimeFormat('en-IN', {
  timeZone: 'Asia/Kolkata',
  day: '2-digit',
  month: 'short',
  hour: '2-digit',
  minute: '2-digit',
  hour12: false,
});
const IST_DATE = new Intl.DateTimeFormat('en-IN', {
  timeZone: 'Asia/Kolkata',
  day: '2-digit',
  month: 'short',
  year: 'numeric',
});

function stamp(frame: Frame | undefined, cadence: Cadence): string {
  if (!frame) return '--';
  return cadence === 'daily' ? IST_DATE.format(frame.at) : `${IST_TIME.format(frame.at)} IST`;
}

function num(value: unknown, digits = 1): string {
  return typeof value === 'number' ? value.toFixed(digits) : '--';
}

/** Crossfade is a CSS transition on the layer container that Leaflet's
 *  setOpacity writes to. Injected once rather than living in index.css so
 *  this component stays self-contained. */
const FADE_STYLE_ID = 'gibs-frame-fade';
function ensureFadeStyle() {
  if (typeof document === 'undefined' || document.getElementById(FADE_STYLE_ID)) return;
  const el = document.createElement('style');
  el.id = FADE_STYLE_ID;
  el.textContent = '.gibs-frame{transition:opacity 260ms ease-in-out}';
  document.head.appendChild(el);
}

/** Retry a failed tile once, then leave it transparent.
 *
 *  Leaflet swaps tile.src to errorTileUrl *before* firing tileerror, so
 *  reading the element's src here would just re-apply the blank fallback.
 *  The real URL has to be rebuilt from the tile coordinates. */
function retryTileOnce(layer: L.TileLayer, event: L.TileErrorEvent) {
  const img = event.tile as HTMLImageElement | undefined;
  if (!img || img.dataset.retried === '1') return;
  img.dataset.retried = '1';
  const url = (layer as unknown as { getTileUrl(coords: L.Coords): string }).getTileUrl(event.coords);
  window.setTimeout(() => {
    if (!img.isConnected) return;
    // Cleared so a successful retry counts as real imagery again.
    delete img.dataset.gibsFailed;
    img.src = url;
  }, TILE_RETRY_MS);
}

const CITY_FIELDS: { key: string; label: string; unit: string }[] = [
  { key: 'temperature_2m', label: 'Temperature', unit: '°C' },
  { key: 'relative_humidity_2m', label: 'Humidity', unit: '%' },
  { key: 'precipitation', label: 'Precipitation', unit: 'mm' },
  { key: 'wind_speed_10m', label: 'Wind', unit: 'm/s' },
  { key: 'wind_gusts_10m', label: 'Gusts', unit: 'm/s' },
  { key: 'pressure_msl', label: 'Pressure', unit: 'hPa' },
];

type FrameStatus = 'loading' | 'ready' | 'failed';

interface PooledFrame {
  layer: L.TileLayer;
  status: FrameStatus;
  timeout: number | null;
  /** Tiles that arrived as real imagery rather than the blank fallback. */
  ok: number;
}

export function LiveCloudView() {
  const [layerId, setLayerId] = useState(CLOUD_LAYERS[0].id);
  const [opacity, setOpacity] = useState(0.85);
  const [frames, setFrames] = useState<Frame[]>([]);
  const [index, setIndex] = useState(0);
  const [shown, setShown] = useState(0);
  const [playing, setPlaying] = useState(false);
  const [interacting, setInteracting] = useState(false);
  const [busy, setBusy] = useState(false);
  const [slow, setSlow] = useState(false);
  const [unavailable, setUnavailable] = useState(false);
  const [cities, setCities] = useState<MapCity[]>([]);
  const [selectedCity, setSelectedCity] = useState<string | null>(null);
  const [cityError, setCityError] = useState<string | null>(null);

  const cfg = useMemo(() => CLOUD_LAYERS.find((l) => l.id === layerId) ?? CLOUD_LAYERS[0], [layerId]);

  const containerRef = useRef<HTMLDivElement | null>(null);
  const [map, setMap] = useState<L.Map | null>(null);
  const poolRef = useRef(new Map<number, PooledFrame>());
  const markersRef = useRef<L.CircleMarker[]>([]);
  const failuresRef = useRef(0);
  // Bumped whenever a pooled frame changes status. Effects that care about
  // pool state depend on this instead of mutating `frames` to force a
  // render - which used to invalidate the pool on every tile that loaded.
  const [poolVersion, setPoolVersion] = useState(0);

  // --- map bootstrap -------------------------------------------------
  useEffect(() => {
    ensureFadeStyle();
    if (!containerRef.current) return;

    const instance = L.map(containerRef.current, {
      center: [22.5, 79],
      zoom: 5,
      minZoom: MIN_ZOOM,
      maxZoom: MAX_ZOOM,
      maxBounds: MAX_BOUNDS,
      maxBoundsViscosity: 0.5,
      // Smoothness: fractional zoom steps, a debounced wheel, and canvas
      // rendering for the city markers so they don't re-layout per frame.
      zoomSnap: 0.5,
      zoomDelta: 0.5,
      // Leaflet's option is wheelPxPerZoomLevel: scroll pixels per zoom
      // level. Double the default of 60, so the wheel zooms at half speed
      // and lands on the half-steps zoomSnap allows.
      wheelPxPerZoomLevel: 120,
      wheelDebounceTime: 60,
      zoomAnimation: true,
      fadeAnimation: true,
      markerZoomAnimation: true,
      preferCanvas: true,
      zoomControl: true,
      attributionControl: true,
    });

    const base = L.tileLayer(BASEMAP_URL, {
      attribution: BASEMAP_ATTRIBUTION,
      subdomains: 'abcd',
      minZoom: MIN_ZOOM,
      maxZoom: MAX_ZOOM,
      noWrap: true,
      bounds: MAX_BOUNDS,
      errorTileUrl: BLANK_TILE,
      keepBuffer: 4,
      updateWhenZooming: false,
      updateWhenIdle: true,
      updateInterval: 200,
      crossOrigin: true,
    });
    base.on('tileerror', (event: L.TileErrorEvent) => retryTileOnce(base, event));
    base.addTo(instance);

    requestAnimationFrame(() => instance.invalidateSize());
    setMap(instance);

    return () => {
      instance.remove();
      setMap(null);
      poolRef.current.forEach((entry) => {
        if (entry.timeout) window.clearTimeout(entry.timeout);
      });
      poolRef.current = new Map();
      markersRef.current = [];
    };
  }, []);

  // --- pause playback while the user is manipulating the map ---------
  useEffect(() => {
    if (!map) return;
    let resume: number | null = null;
    const hold = () => {
      if (resume) window.clearTimeout(resume);
      setInteracting(true);
    };
    const release = () => {
      if (resume) window.clearTimeout(resume);
      resume = window.setTimeout(() => setInteracting(false), RESUME_AFTER_MS);
    };
    map.on('zoomstart movestart dragstart', hold);
    map.on('zoomend moveend dragend', release);
    return () => {
      if (resume) window.clearTimeout(resume);
      map.off('zoomstart movestart dragstart', hold);
      map.off('zoomend moveend dragend', release);
    };
  }, [map]);

  // --- frame pool -----------------------------------------------------
  const clearPool = useCallback(
    (instance: L.Map) => {
      poolRef.current.forEach((entry) => {
        if (entry.timeout) window.clearTimeout(entry.timeout);
        instance.removeLayer(entry.layer);
      });
      poolRef.current = new Map();
    },
    [],
  );

  // Rebuild the timeline whenever the imagery layer changes.
  useEffect(() => {
    if (!map) return;
    clearPool(map);
    const next = buildFrames(cfg);
    failuresRef.current = 0;
    setUnavailable(false);
    setFrames(next);
    setIndex(next.length - 1);
    setShown(next.length - 1);
    setPlaying(false);
  }, [cfg, map, clearPool]);

  /** Create (or reuse) the layer for a frame, mounted transparent so its
   *  tiles are fetched without being seen. */
  const ensureFrame = useCallback(
    (i: number): PooledFrame | null => {
      if (!map || i < 0 || i >= frames.length) return null;
      const existing = poolRef.current.get(i);
      if (existing) return existing;

      const layer = L.tileLayer(tileUrl(cfg, frames[i].time), {
        opacity: 0,
        minZoom: MIN_ZOOM,
        maxZoom: MAX_ZOOM,
        maxNativeZoom: cfg.maxNativeZoom,
        noWrap: true,
        bounds: MAX_BOUNDS,
        errorTileUrl: BLANK_TILE,
        keepBuffer: 4,
        updateWhenZooming: false,
        updateWhenIdle: true,
        updateInterval: 200,
        crossOrigin: true,
        className: 'gibs-frame',
      });
      const entry: PooledFrame = { layer, status: 'loading', timeout: null, ok: 0 };
      const settle = (status: FrameStatus) => {
        if (entry.status !== 'loading') return;
        entry.status = status;
        if (entry.timeout) window.clearTimeout(entry.timeout);
        entry.timeout = null;
        if (status === 'ready') {
          failuresRef.current = 0;
          setUnavailable(false);
        } else {
          failuresRef.current += 1;
          if (failuresRef.current >= FAILURE_LIMIT) setUnavailable(true);
        }
        setPoolVersion((v) => v + 1);
      };

      layer.on('tileerror', (event: L.TileErrorEvent) => {
        const img = event.tile as HTMLImageElement | undefined;
        if (img) img.dataset.gibsFailed = '1';
        retryTileOnce(layer, event);
      });

      layer.on('tileload', (event: L.TileEvent) => {
        const img = event.tile as HTMLImageElement | undefined;
        if (img && img.dataset.gibsFailed !== '1') entry.ok += 1;
      });

      // errorTileUrl makes a 404 resolve as a transparent tile, so Leaflet
      // reports a wholly-missing frame as loaded. Completeness is therefore
      // judged on whether any real tile arrived - without this, frames
      // absent from the archive would silently play as blank instead of
      // being skipped.
      layer.on('load', () => settle(entry.ok > 0 ? 'ready' : 'failed'));
      entry.timeout = window.setTimeout(() => settle('failed'), FRAME_TIMEOUT_MS);

      layer.addTo(map);
      poolRef.current.set(i, entry);
      return entry;
    },
    [map, frames, cfg],
  );

  // Mount the target frame plus the next two, and drop anything outside
  // that window so the map never holds more than a handful of layers.
  useEffect(() => {
    if (!map || !frames.length) return;
    const keep = new Set<number>();
    for (let k = 0; k <= PRELOAD_AHEAD; k++) {
      const i = (index + k) % frames.length;
      keep.add(i);
      ensureFrame(i);
    }
    // The frame currently on screen stays mounted until its replacement
    // has loaded, otherwise the map blanks during the swap.
    keep.add(shown);

    poolRef.current.forEach((entry, i) => {
      if (keep.has(i)) return;
      if (entry.timeout) window.clearTimeout(entry.timeout);
      map.removeLayer(entry.layer);
      poolRef.current.delete(i);
    });
  }, [map, frames, index, shown, ensureFrame]);

  // Swap to the target frame only once it has actually loaded, so the map
  // never flashes a half-drawn frame. A failed frame is skipped forward.
  useEffect(() => {
    if (!frames.length) return;
    const entry = poolRef.current.get(index);
    if (!entry) return;

    if (entry.status === 'ready') {
      setShown(index);
      return;
    }
    if (entry.status === 'failed') {
      for (let step = 1; step <= frames.length; step++) {
        const candidate = (index + step) % frames.length;
        if (poolRef.current.get(candidate)?.status !== 'failed') {
          setIndex(candidate);
          return;
        }
      }
    }
  }, [index, frames.length, poolVersion]);

  // --- apply opacity / crossfade -------------------------------------
  useEffect(() => {
    poolRef.current.forEach((entry, i) => entry.layer.setOpacity(i === shown ? opacity : 0));
  }, [shown, opacity, poolVersion]);

  // --- spinner --------------------------------------------------------
  useEffect(() => {
    const pending = poolRef.current.get(index)?.status === 'loading';
    if (!pending) {
      setBusy(false);
      setSlow(false);
      return;
    }
    // Delayed so an already-cached frame never flashes the spinner.
    const show = window.setTimeout(() => setBusy(true), SPINNER_DELAY_MS);
    const slowTimer = window.setTimeout(() => setSlow(true), SLOW_AFTER_MS);
    return () => {
      window.clearTimeout(show);
      window.clearTimeout(slowTimer);
    };
  }, [index, poolVersion]);

  // --- playback -------------------------------------------------------
  useEffect(() => {
    if (!playing || interacting || frames.length === 0) return;
    const timer = window.setInterval(() => {
      // Only advance once the current frame is on screen, so a slow frame
      // stretches the animation instead of dropping it.
      if (poolRef.current.get(index)?.status === 'loading') return;
      setIndex((prev) => (prev + 1) % frames.length);
    }, PLAYBACK_MS);
    return () => window.clearInterval(timer);
  }, [playing, interacting, frames.length, index]);

  // --- city pins ------------------------------------------------------
  useEffect(() => {
    fetchMapCities()
      .then((res) => {
        setCities(res.cities);
        setSelectedCity((prev) => prev ?? res.cities[0]?.id ?? null);
      })
      .catch((err) => setCityError((err as Error).message));
  }, []);

  useEffect(() => {
    if (!map) return;
    for (const marker of markersRef.current) map.removeLayer(marker);
    markersRef.current = cities.map((city) => {
      const active = city.id === selectedCity;
      const marker = L.circleMarker([city.latitude, city.longitude], {
        radius: active ? 8 : 6,
        color: active ? '#F8FAFC' : '#38BDF8',
        weight: 2,
        fillColor: '#38BDF8',
        fillOpacity: active ? 1 : 0.65,
      });
      marker.bindTooltip(city.name, { direction: 'top', offset: [0, -6] });
      marker.on('click', () => setSelectedCity(city.id));
      marker.addTo(map);
      return marker;
    });
  }, [cities, selectedCity, map]);

  const city = cities.find((c) => c.id === selectedCity) ?? null;
  const frame = frames[shown];

  return (
    <section className="rounded-2xl border border-white/10 bg-slate-900/60 p-4 shadow-2xl backdrop-blur-xl sm:p-6">
      <header className="mb-5">
        <h2 className="flex items-center gap-2 font-display text-xl text-textPrimary sm:text-2xl">
          <Satellite className="h-5 w-5 text-accent" />
          Live Cloud View
        </h2>
        <p className="mt-1 font-sans text-sm text-textMuted">
          NASA GIBS satellite imagery over India. Geostationary frames step every {STEP_MINUTES} minutes across the
          last {(GEO_FRAMES * STEP_MINUTES) / 60} hours.
        </p>
      </header>

      <div className="mb-4 flex flex-wrap items-end gap-3">
        <label className="flex w-full flex-col gap-1.5 sm:w-auto">
          <span className="font-mono text-[10px] uppercase tracking-widest text-textMuted">Imagery layer</span>
          <select
            value={layerId}
            onChange={(e) => setLayerId(e.target.value)}
            className="w-full rounded-lg border border-white/10 bg-black/30 px-3 py-2 font-sans text-base text-textPrimary transition-colors focus:border-accent/50 focus:outline-none sm:w-72 sm:text-sm"
          >
            {CLOUD_LAYERS.map((l) => (
              <option key={l.id} value={l.id} className="bg-slate-900">
                {l.label}
              </option>
            ))}
          </select>
        </label>

        <label className="flex w-full flex-col gap-1.5 sm:w-48">
          <span className="font-mono text-[10px] uppercase tracking-widest text-textMuted">
            Cloud opacity - {Math.round(opacity * 100)}%
          </span>
          <input
            type="range"
            min={0}
            max={1}
            step={0.05}
            value={opacity}
            onChange={(e) => setOpacity(Number(e.target.value))}
            className="accent-sky-400"
          />
        </label>
      </div>

      {cfg.caveat && (
        <p className="mb-4 flex items-start gap-2 rounded-xl border border-white/10 bg-black/25 p-3 font-mono text-[11px] text-textMuted">
          <AlertCircle className="mt-0.5 h-3.5 w-3.5 flex-shrink-0" />
          <span>{cfg.caveat}</span>
        </p>
      )}

      <div className="grid grid-cols-1 gap-4 lg:grid-cols-[1fr_17rem]">
        <div>
          <div className="relative">
            {/* Lenis drives page scrolling globally; without this the wheel
                gesture scrolls the page instead of zooming the map. */}
            <div
              ref={containerRef}
              data-lenis-prevent
              className="relative z-0 h-[320px] w-full overflow-hidden rounded-xl border border-white/10 bg-black/40 sm:h-[440px]"
            />

            {/* Corner overlay - never blocks the map. */}
            <div
              className={`pointer-events-none absolute bottom-3 left-3 z-[400] flex items-center gap-2 rounded-lg border border-white/10 bg-black/70 px-3 py-2 font-mono text-[11px] text-textPrimary backdrop-blur-sm transition-opacity duration-200 ${
                busy ? 'opacity-100' : 'opacity-0'
              }`}
            >
              <Loader2 className="h-3.5 w-3.5 animate-spin text-accent" />
              {slow ? 'Satellite servers are slow, still loading...' : 'Loading satellite imagery...'}
            </div>

            {unavailable && (
              <div className="pointer-events-none absolute top-3 left-3 z-[400] flex items-center gap-2 rounded-lg border border-alertRed/40 bg-black/75 px-3 py-2 font-mono text-[11px] text-alertRed backdrop-blur-sm transition-opacity duration-200">
                <AlertCircle className="h-3.5 w-3.5" />
                Satellite imagery temporarily unavailable
              </div>
            )}
          </div>

          <div className="mt-3 flex flex-wrap items-center gap-3">
            <button
              onClick={() => setPlaying((p) => !p)}
              disabled={frames.length === 0}
              className="flex items-center gap-2 rounded-lg border border-accent/30 bg-accent/10 px-3 py-2 font-mono text-xs uppercase tracking-widest text-accent transition-colors hover:bg-accent/20 disabled:opacity-50"
            >
              {playing ? <Pause className="h-4 w-4" /> : <Play className="h-4 w-4" />}
              {playing ? 'Pause' : 'Play'}
            </button>

            <input
              type="range"
              min={0}
              max={Math.max(frames.length - 1, 0)}
              step={1}
              value={index}
              onChange={(e) => {
                setPlaying(false);
                setIndex(Number(e.target.value));
              }}
              className="min-w-[8rem] flex-1 accent-sky-400"
            />

            <span className="font-mono text-xs text-textPrimary">{stamp(frame, cfg.cadence)}</span>
          </div>

          <p className="mt-2 font-mono text-[11px] text-textMuted">
            Imagery is delayed - geostationary frames reach GIBS roughly {GEO_LAG_MINUTES} minutes after capture, so
            this is recent, not live.
          </p>
        </div>

        <aside className="rounded-xl border border-white/5 bg-black/25 p-4">
          <h3 className="mb-1 font-mono text-[10px] uppercase tracking-widest text-textMuted">Current conditions</h3>
          {cityError && <p className="font-mono text-[11px] text-alertRed">{cityError}</p>}
          {!city && !cityError && <p className="font-mono text-[11px] text-textMuted">Loading cities...</p>}

          {city && (
            <>
              <p className="mb-3 font-display text-xl text-textPrimary">{city.name}</p>
              <dl className="space-y-1.5">
                {CITY_FIELDS.map((f) => (
                  <div key={f.key} className="flex items-baseline justify-between gap-2">
                    <dt className="font-sans text-xs text-textMuted">{f.label}</dt>
                    <dd className="font-mono text-xs text-textPrimary">
                      {num(city.current[f.key])} {f.unit}
                    </dd>
                  </div>
                ))}
              </dl>

              <h4 className="mt-4 mb-1 font-mono text-[10px] uppercase tracking-widest text-textMuted">Next days</h4>
              <ul className="space-y-1">
                {(city.daily.time ?? []).slice(0, 3).map((day, i) => (
                  <li key={String(day)} className="flex items-baseline justify-between gap-2">
                    <span className="font-sans text-xs text-textMuted">{String(day).slice(5)}</span>
                    <span className="font-mono text-[11px] text-textPrimary">
                      {num(city.daily.precipitation_sum?.[i])} mm ·{' '}
                      {num(city.daily.precipitation_probability_max?.[i], 0)}%
                    </span>
                  </li>
                ))}
              </ul>

              <p className="mt-4 font-mono text-[10px] text-textMuted">
                Click any pin to switch city. Values come from the same Open-Meteo integration as the rest of the app.
              </p>
            </>
          )}
        </aside>
      </div>
    </section>
  );
}
