import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import L from 'leaflet';
import 'leaflet/dist/leaflet.css';
import {
  addLeafletProtocolSupport,
  getColorScale,
  omProtocol,
  updateCurrentBounds,
} from '@openmeteo/weather-map-layer';
import { AlertCircle, Loader2, Pause, Play, Satellite } from 'lucide-react';
import { fetchMapCities, type MapCity } from '../../lib/research';
import {
  cachedSeries,
  compass16,
  loadWindSeries,
  nearestCachedSeries,
  nearestCity,
  readingAt,
  underRateLimit,
  type WindSeries,
} from '../../lib/wind';

/**
 * Live Cloud View - NASA GIBS imagery plus a DWD ICON wind field on one
 * Leaflet map.
 *
 * Every GIBS layer identifier, tile matrix set, format and zoom ceiling below
 * was read out of the live WMTS capabilities document
 * (gibs.earthdata.nasa.gov/wmts/epsg3857/best/1.0.0/WMTSCapabilities.xml) and
 * then confirmed with real tile requests. Do not edit them from memory: the
 * matrix set encodes the layer's maximum native zoom, and GIBS answers
 * anything above it with an HTML/XML error body rather than an image, which a
 * browser renders as a broken tile.
 *
 * GIBS REST tiles are ordered {TileMatrix}/{TileRow}/{TileCol} = {z}/{y}/{x},
 * not Leaflet's default {z}/{x}/{y}.
 *
 * GIBS serves tiles with `Cache-Control: no-store`, so nothing it returns is
 * ever reused from the browser cache. That is why the cloud animation frames
 * are pooled (current plus the next two) instead of all being mounted at once.
 *
 * Layer stack, bottom to top:
 *   0. BlueMarble_ShadedRelief_Bathymetry - static base
 *   1. VIIRS_SNPP_CorrectedReflectance_TrueColor - optional daily true color
 *   2. Himawari_AHI_Band13_Clean_Infrared - animated clouds
 *   3. DWD ICON wind_speed_10m - Open-Meteo om:// layer
 *   4. Coastlines_15m + Reference_Features_15m - static reference
 */

// GIBS speaks HTTP/1.1, so a browser opens at most 6 connections per origin.
// This map stacks several tile layers, which is dozens of tiles on first paint
// - funnelled through one origin that queues into a black map for ten seconds
// or more. NASA publishes gibs-a/b/c as equivalent hosts for exactly this
// reason, so {s} spreads the burst over three origins (18 connections). All
// three are still GIBS; no third-party tile provider is involved.
const GIBS_BASE = 'https://gibs-{s}.earthdata.nasa.gov/wmts/epsg3857/best';
const GIBS_SUBDOMAINS = 'abc';
const ATTRIBUTION = 'Imagery: NASA EOSDIS GIBS &middot; Wind: DWD ICON via Open-Meteo';

/** Open-Meteo's spatial ICON archive, read through the om:// protocol. */
const OM_LATEST = 'https://openmeteo.s3.amazonaws.com/data_spatial/dwd_icon/latest.json';
const WIND_VARIABLE = 'wind_speed_10m';

/** 1x1 transparent PNG. Any tile that fails resolves to this, so a broken
 *  image icon or a provider's error page is never visible. */
const BLANK_TILE =
  'data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNkYPhfDwAChwGA60e6kgAAAABJRU5ErkJggg==';

const MIN_ZOOM = 3;
const MAX_ZOOM = 9;
// India plus the surrounding ocean. Keeps the viewport inside the region the
// geostationary disk actually covers.
const MAX_BOUNDS = L.latLngBounds([-15, 35], [50, 125]);

interface GibsLayerConfig {
  layer: string;
  matrixSet: string;
  ext: 'png' | 'jpeg';
  /** Highest zoom the matrix set publishes; above this Leaflet upscales
   *  instead of requesting a tile that does not exist. */
  maxNativeZoom: number;
}

// Static base - shaded relief + bathymetry, dark and legible under clouds.
const BASE_LAYER: GibsLayerConfig = {
  layer: 'BlueMarble_ShadedRelief_Bathymetry',
  matrixSet: 'GoogleMapsCompatible_Level8',
  ext: 'jpeg',
  maxNativeZoom: 8,
};

// Optional daily true-color overlay, defaulting to yesterday (UTC) since
// today's composite is still being assembled for most of the day.
//
// VIIRS rather than MODIS: MODIS's 2330 km swath is narrower than the spacing
// between consecutive orbits, so its daily composite has wedge-shaped gaps
// between passes. The product is JPEG, which has no alpha, so those gaps are
// opaque black painted straight over the base map. VIIRS's 3040 km swath
// overlaps between orbits and leaves none - measured 0% black against MODIS's
// 1.5-2.4% on the same tiles.
const TRUE_COLOR_LAYER: GibsLayerConfig = {
  layer: 'VIIRS_SNPP_CorrectedReflectance_TrueColor',
  matrixSet: 'GoogleMapsCompatible_Level9',
  ext: 'jpeg',
  maxNativeZoom: 9,
};

const CLOUD_LAYER: GibsLayerConfig = {
  layer: 'Himawari_AHI_Band13_Clean_Infrared',
  matrixSet: 'GoogleMapsCompatible_Level6',
  ext: 'png',
  maxNativeZoom: 6,
};

const COASTLINES_LAYER: GibsLayerConfig = {
  layer: 'Coastlines_15m',
  matrixSet: 'GoogleMapsCompatible_Level13',
  ext: 'png',
  maxNativeZoom: 13,
};
const BORDERS_LAYER: GibsLayerConfig = {
  layer: 'Reference_Features_15m',
  matrixSet: 'GoogleMapsCompatible_Level13',
  ext: 'png',
  maxNativeZoom: 13,
};
// Reference_Labels_15m is deliberately absent. Over India it serves no label
// content at all, and GIBS encodes its empty tiles two different ways: a
// correct transparent PNG, and a 921-byte palette PNG that is 100% opaque
// black. At the zooms this map uses most tiles come back as the black variant
// (11 of 16 sampled at z5/z6), so the layer only ever painted black boxes over
// the imagery. Coastlines and borders carry the readability instead.

const STEP_MINUTES = 10;
const GEO_FRAMES = 36; // 6 hours of cloud frames
const GEO_LAG_MINUTES = 40;
/** How far ahead of now the shared timeline runs. */
const FORECAST_HOURS = 48;

const FRAME_TIMEOUT_MS = 6000;
const TILE_RETRY_MS = 1000;
const PLAYBACK_MS = 700;
const RESUME_AFTER_MS = 500;
const SPINNER_DELAY_MS = 150;
const SLOW_AFTER_MS = 8000;
const PRELOAD_AHEAD = 2;
const FAILURE_LIMIT = 3;
const REFERENCE_DELAY_MS = 4000;
const CURSOR_DEBOUNCE_MS = 250;
const CURSOR_TIMEOUT_MS = 5000;

interface Frame {
  time: string;
  at: Date;
}

/** Newest cloud frame GIBS is likely to hold, rounded to the 10-minute grid. */
function newestCloudTime(): number {
  const stepMs = STEP_MINUTES * 60_000;
  return Math.floor((Date.now() - GEO_LAG_MINUTES * 60_000) / stepMs) * stepMs;
}

function buildFrames(): Frame[] {
  const stepMs = STEP_MINUTES * 60_000;
  const newest = newestCloudTime();
  const out: Frame[] = [];
  for (let i = GEO_FRAMES - 1; i >= 0; i--) {
    const at = new Date(newest - i * stepMs);
    out.push({ time: at.toISOString().replace('.000Z', 'Z'), at });
  }
  return out;
}

function yesterdayUTC(): string {
  return new Date(Date.now() - 86_400_000).toISOString().slice(0, 10);
}

function staticUrl(cfg: GibsLayerConfig, time?: string): string {
  return time
    ? `${GIBS_BASE}/${cfg.layer}/default/${time}/${cfg.matrixSet}/{z}/{y}/{x}.${cfg.ext}`
    : `${GIBS_BASE}/${cfg.layer}/default/${cfg.matrixSet}/{z}/{y}/{x}.${cfg.ext}`;
}

function cloudUrl(time: string): string {
  return `${GIBS_BASE}/${CLOUD_LAYER.layer}/default/${time}/${CLOUD_LAYER.matrixSet}/{z}/{y}/{x}.${CLOUD_LAYER.ext}`;
}

/** The om:// URL for one ICON forecast hour. `valid_times_<n>` indexes the
 *  array published by latest.json, which is why the step keeps its original
 *  position rather than its position in the trimmed timeline. */
function windUrl(iconIndex: number): string {
  return `om://${OM_LATEST}?time_step=valid_times_${iconIndex}&variable=${WIND_VARIABLE}`;
}

const IST_TIME = new Intl.DateTimeFormat('en-IN', {
  timeZone: 'Asia/Kolkata',
  day: '2-digit',
  month: 'short',
  hour: '2-digit',
  minute: '2-digit',
  hour12: false,
});

function num(value: unknown, digits = 1): string {
  return typeof value === 'number' ? value.toFixed(digits) : '--';
}

function coordLabel(lat: number, lon: number): string {
  const ns = `${Math.abs(lat).toFixed(1)}°${lat >= 0 ? 'N' : 'S'}`;
  const ew = `${Math.abs(lon).toFixed(1)}°${lon >= 0 ? 'E' : 'W'}`;
  return `${ns} ${ew}`;
}

/** Crossfade is a CSS transition on the layer container that Leaflet's
 *  setOpacity writes to. Injected once rather than living in index.css so
 *  this component stays self-contained. */
const FADE_STYLE_ID = 'gibs-frame-fade';
function ensureFadeStyle() {
  if (typeof document === 'undefined' || document.getElementById(FADE_STYLE_ID)) return;
  const el = document.createElement('style');
  el.id = FADE_STYLE_ID;
  el.textContent =
    '.gibs-frame{transition:opacity 260ms ease-in-out}.wind-cursor{transition:opacity 120ms ease-in-out}';
  document.head.appendChild(el);
}

/** Register the om:// protocol against Leaflet exactly once. */
let omAdapter: ReturnType<typeof addLeafletProtocolSupport> | null = null;
function getOmAdapter() {
  if (!omAdapter) {
    omAdapter = addLeafletProtocolSupport(L);
    omAdapter.addProtocol('om', omProtocol);
  }
  return omAdapter;
}

/** Retry a failed tile once, then leave it transparent.
 *
 *  Leaflet swaps tile.src to errorTileUrl *before* firing tileerror, so
 *  reading the element's src here would just re-apply the blank fallback.
 *  The real URL has to be rebuilt from the tile coordinates. This is also
 *  what absorbs GIBS's occasional one-off 500s so they never surface as
 *  visible error artwork. */
function retryTileOnce(layer: L.TileLayer, event: L.TileErrorEvent) {
  const img = event.tile as HTMLImageElement | undefined;
  if (!img || img.dataset.retried === '1') return;
  img.dataset.retried = '1';
  const url = (layer as unknown as { getTileUrl(coords: L.Coords): string }).getTileUrl(event.coords);
  window.setTimeout(() => {
    if (!img.isConnected) return;
    delete img.dataset.gibsFailed;
    img.src = url;
  }, TILE_RETRY_MS);
}

function makeStaticLayer(
  cfg: GibsLayerConfig,
  opts: { time?: string; zIndex: number; attribution?: string },
): L.TileLayer {
  const layer = L.tileLayer(staticUrl(cfg, opts.time), {
    subdomains: GIBS_SUBDOMAINS,
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
    zIndex: opts.zIndex,
    attribution: opts.attribution,
  });
  layer.on('tileerror', (event: L.TileErrorEvent) => retryTileOnce(layer, event));
  return layer;
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

/** One entry on the shared timeline: a 10-minute cloud frame in the past, or
 *  an ICON forecast hour in the future. */
interface TimelineStep {
  at: Date;
  /** Index into latest.json's valid_times, needed for time_step=valid_times_N;
   *  -1 when no ICON hour is within half an hour of this step. */
  iconIndex: number;
  /** Nearest cloud frame, or -1 when the hour is past the newest imagery. */
  cloudIndex: number;
}

interface CursorState {
  x: number;
  y: number;
  place: string;
  series: WindSeries | null;
  status: 'loading' | 'ok' | 'stale' | 'error';
}

/** Legend built from the layer's own scale so the colours always agree with
 *  the pixels. The scale is published in m/s; the map labels km/h. */
function WindLegend() {
  const scale = useMemo(() => {
    try {
      return getColorScale(WIND_VARIABLE, true);
    } catch {
      return null;
    }
  }, []);

  if (!scale?.breakpoints?.length || !scale.colors?.length) return null;

  const stops = scale.breakpoints;
  const span = stops[stops.length - 1] - stops[0] || 1;
  const gradient = stops
    .map((bp, i) => {
      const c = scale.colors[Math.min(i, scale.colors.length - 1)];
      const pct = ((bp - stops[0]) / span) * 100;
      return `rgba(${c[0]},${c[1]},${c[2]},${c[3]}) ${pct.toFixed(1)}%`;
    })
    .join(', ');

  // A handful of readable km/h ticks rather than all 23 breakpoints.
  const ticks = [0, 5, 10, 20, 30, 60].filter((ms) => ms <= stops[stops.length - 1]);

  return (
    <div className="mt-3">
      <div className="mb-1 flex items-center justify-between font-mono text-[10px] uppercase tracking-widest text-textMuted">
        <span>Wind speed (km/h)</span>
      </div>
      <div
        className="h-2 w-full rounded-full border border-white/10"
        style={{ backgroundImage: `linear-gradient(to right, ${gradient})` }}
      />
      <div className="mt-1 flex justify-between font-mono text-[10px] text-textMuted">
        {ticks.map((ms) => (
          <span key={ms}>{Math.round(ms * 3.6)}</span>
        ))}
      </div>
    </div>
  );
}

export function LiveCloudView() {
  const [showClouds, setShowClouds] = useState(true);
  const [cloudOpacity, setCloudOpacity] = useState(0.85);
  const [showWind, setShowWind] = useState(true);
  const [windOpacity, setWindOpacity] = useState(0.55);
  const [showTrueColor, setShowTrueColor] = useState(false);

  const frames = useMemo(() => buildFrames(), []);
  const [iconTimes, setIconTimes] = useState<string[] | null>(null);
  // Start on the newest cloud frame so imagery is visible on first paint.
  const [stepIndex, setStepIndex] = useState(GEO_FRAMES - 1);
  const [timelineError, setTimelineError] = useState(false);

  // Every cloud frame is a step, so playback animates at the imagery's native
  // 10-minute cadence; ICON hours past the newest frame follow for wind only.
  const steps = useMemo<TimelineStep[]>(() => {
    const icon = (iconTimes ?? []).map((iso) => new Date(iso).getTime());
    const nearestIcon = (ms: number) => {
      let best = -1;
      let gap = Infinity;
      icon.forEach((t, i) => {
        const g = Math.abs(t - ms);
        if (g < gap) {
          gap = g;
          best = i;
        }
      });
      return gap <= 30 * 60_000 ? best : -1;
    };
    const past = frames.map((f, i) => ({ at: f.at, cloudIndex: i, iconIndex: nearestIcon(f.at.getTime()) }));
    const last = frames[frames.length - 1].at.getTime();
    const until = Date.now() + FORECAST_HOURS * 3600_000;
    const future = icon.flatMap((ms, iconIndex) =>
      ms > last && ms <= until ? [{ at: new Date(ms), iconIndex, cloudIndex: -1 }] : [],
    );
    return [...past, ...future];
  }, [frames, iconTimes]);

  const [shown, setShown] = useState(-1);
  const [playing, setPlaying] = useState(false);
  const [interacting, setInteracting] = useState(false);
  const [busy, setBusy] = useState(false);
  const [slow, setSlow] = useState(false);
  const [unavailable, setUnavailable] = useState(false);
  const [cities, setCities] = useState<MapCity[]>([]);
  const [selectedCity, setSelectedCity] = useState<string | null>(null);
  const [cityError, setCityError] = useState<string | null>(null);
  const [cursor, setCursor] = useState<CursorState | null>(null);

  const trueColorDate = useMemo(() => yesterdayUTC(), []);

  const containerRef = useRef<HTMLDivElement | null>(null);
  const [map, setMap] = useState<L.Map | null>(null);
  const poolRef = useRef(new Map<number, PooledFrame>());
  const markersRef = useRef<L.CircleMarker[]>([]);
  const failuresRef = useRef(0);
  const [poolVersion, setPoolVersion] = useState(0);
  const [baseReady, setBaseReady] = useState(false);

  const step = steps[stepIndex];
  const cloudIndex = step ? step.cloudIndex : -1;
  const isFuture = !!step && step.cloudIndex === -1;
  const cloudsVisible = showClouds && !isFuture;

  // --- shared timeline from the ICON run ------------------------------
  useEffect(() => {
    let cancelled = false;
    const controller = new AbortController();
    fetch(OM_LATEST, { signal: controller.signal })
      .then((r) => (r.ok ? r.json() : Promise.reject(new Error(String(r.status)))))
      .then((body: { valid_times?: string[] }) => {
        if (cancelled) return;
        if (!body.valid_times?.length) {
          setTimelineError(true);
          return;
        }
        setIconTimes(body.valid_times);
      })
      .catch(() => {
        if (!cancelled) setTimelineError(true);
      });
    return () => {
      cancelled = true;
      controller.abort();
    };
  }, []);

  // --- map bootstrap: base imagery only -------------------------------
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

    const base = makeStaticLayer(BASE_LAYER, { zIndex: 0, attribution: ATTRIBUTION });
    base.addTo(instance);

    // The reference overlays are cosmetic, so they wait for the base imagery
    // instead of racing it - mounting every layer at once puts ~24 tiles in
    // the queue ahead of the map the user actually needs to see. The timeout
    // is a fallback, because `load` never fires if every base tile fails.
    base.once('load', () => setBaseReady(true));
    const readyFallback = window.setTimeout(() => setBaseReady(true), REFERENCE_DELAY_MS);

    // The om:// protocol reads only the grid window the map is showing.
    const pushBounds = () => {
      const b = instance.getBounds();
      updateCurrentBounds([b.getWest(), b.getSouth(), b.getEast(), b.getNorth()]);
    };
    instance.on('moveend zoomend', pushBounds);
    pushBounds();

    // The panel sits in a responsive grid and the charts below it can add or
    // remove a scrollbar, so the container's width changes after mount. A
    // one-shot invalidateSize() would leave Leaflet with a stale size and a
    // partly unfilled map, so track the container instead.
    const resize = new ResizeObserver(() => instance.invalidateSize());
    resize.observe(containerRef.current);
    setMap(instance);

    return () => {
      resize.disconnect();
      window.clearTimeout(readyFallback);
      instance.off('moveend zoomend', pushBounds);
      setBaseReady(false);
      instance.remove();
      setMap(null);
      poolRef.current.forEach((entry) => {
        if (entry.timeout) window.clearTimeout(entry.timeout);
      });
      poolRef.current = new Map();
      markersRef.current = [];
    };
  }, []);

  // --- coastlines + borders, once the base imagery is in --------------
  useEffect(() => {
    if (!map || !baseReady) return;
    const coastlines = makeStaticLayer(COASTLINES_LAYER, { zIndex: 4 });
    const borders = makeStaticLayer(BORDERS_LAYER, { zIndex: 5 });
    coastlines.addTo(map);
    borders.addTo(map);
    return () => {
      map.removeLayer(coastlines);
      map.removeLayer(borders);
    };
  }, [map, baseReady]);

  // --- optional true-color overlay ------------------------------------
  useEffect(() => {
    if (!map || !showTrueColor) return;
    const layer = makeStaticLayer(TRUE_COLOR_LAYER, { time: trueColorDate, zIndex: 1 });
    layer.addTo(map);
    return () => {
      map.removeLayer(layer);
    };
  }, [map, showTrueColor, trueColorDate]);

  // --- wind field ------------------------------------------------------
  const iconIndex = step ? step.iconIndex : -1;
  useEffect(() => {
    if (!map || !showWind || iconIndex < 0) return;
    let layer: L.Layer | null = null;
    try {
      const adapter = getOmAdapter();
      layer = adapter.createTileLayer(windUrl(iconIndex), {
        opacity: windOpacity,
        zIndex: 3,
        pane: 'tilePane',
      }) as unknown as L.Layer;
      layer.addTo(map);
    } catch {
      layer = null;
    }
    return () => {
      if (layer) map.removeLayer(layer);
    };
  }, [map, showWind, iconIndex, windOpacity]);

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

  // --- cursor wind readout ---------------------------------------------
  useEffect(() => {
    if (!map) return;
    let debounce: number | null = null;
    let timeout: number | null = null;
    let inflight: AbortController | null = null;

    const clearTimers = () => {
      if (debounce) window.clearTimeout(debounce);
      if (timeout) window.clearTimeout(timeout);
      debounce = null;
      timeout = null;
    };

    const hide = () => {
      clearTimers();
      inflight?.abort();
      inflight = null;
      setCursor(null);
    };

    const resolve = (ev: L.LeafletMouseEvent) => {
      const { lat, lng } = ev.latlng;
      const { x, y } = ev.containerPoint;
      const city = nearestCity(lat, lng);
      const qLat = city ? city.latitude : lat;
      const qLon = city ? city.longitude : lng;
      const place = city ? city.name : coordLabel(lat, lng);

      const hit = cachedSeries(qLat, qLon);
      if (hit) {
        setCursor({ x, y, place, series: hit, status: 'ok' });
        return;
      }
      if (!underRateLimit()) {
        const near = nearestCachedSeries(qLat, qLon);
        setCursor({ x, y, place, series: near, status: near ? 'stale' : 'error' });
        return;
      }

      inflight?.abort();
      const controller = new AbortController();
      inflight = controller;
      setCursor({ x, y, place, series: null, status: 'loading' });

      timeout = window.setTimeout(() => {
        controller.abort();
        const near = nearestCachedSeries(qLat, qLon);
        setCursor({ x, y, place, series: near, status: near ? 'stale' : 'error' });
      }, CURSOR_TIMEOUT_MS);

      loadWindSeries(qLat, qLon, controller.signal)
        .then((series) => {
          if (controller.signal.aborted) return;
          if (timeout) window.clearTimeout(timeout);
          setCursor({ x, y, place, series, status: 'ok' });
        })
        .catch(() => {
          if (controller.signal.aborted) return;
          if (timeout) window.clearTimeout(timeout);
          const near = nearestCachedSeries(qLat, qLon);
          setCursor({ x, y, place, series: near, status: near ? 'stale' : 'error' });
        });
    };

    const onMove = (ev: L.LeafletMouseEvent) => {
      const { x, y } = ev.containerPoint;
      // Move the pill immediately so it tracks the pointer, and only hit the
      // network once the cursor has settled.
      setCursor((prev) => (prev ? { ...prev, x, y } : prev));
      if (debounce) window.clearTimeout(debounce);
      debounce = window.setTimeout(() => resolve(ev), CURSOR_DEBOUNCE_MS);
    };

    map.on('mousemove', onMove);
    map.on('click', resolve);
    map.on('mouseout dragstart zoomstart movestart', hide);
    return () => {
      map.off('mousemove', onMove);
      map.off('click', resolve);
      map.off('mouseout dragstart zoomstart movestart', hide);
      clearTimers();
      inflight?.abort();
    };
  }, [map]);

  // --- cloud frame pool -------------------------------------------------
  const clearPool = useCallback((instance: L.Map) => {
    poolRef.current.forEach((entry) => {
      if (entry.timeout) window.clearTimeout(entry.timeout);
      instance.removeLayer(entry.layer);
    });
    poolRef.current = new Map();
  }, []);

  useEffect(() => {
    if (!map) return;
    return () => clearPool(map);
  }, [map, clearPool]);

  const ensureFrame = useCallback(
    (i: number): PooledFrame | null => {
      if (!map || i < 0 || i >= frames.length) return null;
      const existing = poolRef.current.get(i);
      if (existing) return existing;

      const layer = L.tileLayer(cloudUrl(frames[i].time), {
        opacity: 0,
        subdomains: GIBS_SUBDOMAINS,
        minZoom: MIN_ZOOM,
        maxZoom: MAX_ZOOM,
        maxNativeZoom: CLOUD_LAYER.maxNativeZoom,
        noWrap: true,
        bounds: MAX_BOUNDS,
        errorTileUrl: BLANK_TILE,
        keepBuffer: 4,
        updateWhenZooming: false,
        updateWhenIdle: true,
        updateInterval: 200,
        crossOrigin: true,
        zIndex: 2,
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
      // judged on whether any real tile arrived.
      layer.on('load', () => settle(entry.ok > 0 ? 'ready' : 'failed'));
      entry.timeout = window.setTimeout(() => settle('failed'), FRAME_TIMEOUT_MS);

      layer.addTo(map);
      poolRef.current.set(i, entry);
      return entry;
    },
    [map, frames],
  );

  // Mount the visible frame plus the next two, and drop anything outside
  // that window so the map never holds more than a handful of layers.
  useEffect(() => {
    if (!map) return;
    if (!cloudsVisible || cloudIndex < 0) {
      clearPool(map);
      return;
    }
    const keep = new Set<number>();
    keep.add(cloudIndex);
    ensureFrame(cloudIndex);

    // Preloading only starts once the visible frame has settled. Mounting all
    // three frames up front puts ~24 speculative tiles in front of the base
    // map in the connection queue, which is what made the first paint blank.
    if (poolRef.current.get(cloudIndex)?.status !== 'loading') {
      for (let k = 1; k <= PRELOAD_AHEAD; k++) {
        const i = (cloudIndex + k) % frames.length;
        keep.add(i);
        ensureFrame(i);
      }
    }
    if (shown >= 0) keep.add(shown);

    poolRef.current.forEach((entry, i) => {
      if (keep.has(i)) return;
      if (entry.timeout) window.clearTimeout(entry.timeout);
      map.removeLayer(entry.layer);
      poolRef.current.delete(i);
    });
  }, [map, frames, cloudIndex, cloudsVisible, shown, ensureFrame, clearPool, poolVersion]);

  // Swap to the target frame only once it has loaded, so the map never
  // flashes a half-drawn frame.
  useEffect(() => {
    // No reset when the hour has no imagery: the opacity effect already
    // hides every pooled frame once cloudsVisible goes false.
    if (cloudIndex < 0) return;
    if (poolRef.current.get(cloudIndex)?.status === 'ready') setShown(cloudIndex);
  }, [cloudIndex, poolVersion]);

  // --- apply opacity / crossfade -------------------------------------
  useEffect(() => {
    poolRef.current.forEach((entry, i) =>
      entry.layer.setOpacity(i === shown && cloudsVisible ? cloudOpacity : 0),
    );
  }, [shown, cloudOpacity, cloudsVisible, poolVersion]);

  // --- spinner --------------------------------------------------------
  useEffect(() => {
    const pending = cloudsVisible && cloudIndex >= 0 && poolRef.current.get(cloudIndex)?.status === 'loading';
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
  }, [cloudIndex, cloudsVisible, poolVersion]);

  // --- playback -------------------------------------------------------
  useEffect(() => {
    if (!playing || interacting || steps.length === 0) return;
    // With clouds on, loop the imagery rather than running on into forecast
    // hours where there are no clouds to animate.
    const loopEnd = showClouds ? frames.length : steps.length;
    const timer = window.setInterval(() => {
      // Only advance once the current cloud frame is on screen, so a slow
      // frame stretches the animation instead of dropping it.
      if (cloudIndex >= 0 && poolRef.current.get(cloudIndex)?.status === 'loading') return;
      setStepIndex((prev) => (prev + 1 >= loopEnd ? 0 : prev + 1));
    }, PLAYBACK_MS);
    return () => window.clearInterval(timer);
  }, [playing, interacting, steps.length, frames.length, showClouds, cloudIndex]);

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
    markersRef.current = cities.map((c) => {
      const active = c.id === selectedCity;
      const marker = L.circleMarker([c.latitude, c.longitude], {
        radius: active ? 8 : 6,
        color: active ? '#F8FAFC' : '#38BDF8',
        weight: 2,
        fillColor: '#38BDF8',
        fillOpacity: active ? 1 : 0.65,
        pane: 'markerPane',
      });
      marker.bindTooltip(c.name, { direction: 'top', offset: [0, -6] });
      marker.on('click', () => setSelectedCity(c.id));
      marker.addTo(map);
      return marker;
    });
  }, [cities, selectedCity, map]);

  const city = cities.find((c) => c.id === selectedCity) ?? null;

  // Cursor label text, derived at render so dragging the timeline re-reads
  // the cached series instead of firing another request.
  const cursorLabel = useMemo(() => {
    if (!cursor) return null;
    if (cursor.status === 'loading') return { text: 'Loading…', arrow: null as number | null };
    if (!cursor.series || !step) return { text: 'Wind unavailable', arrow: null };
    const reading = readingAt(cursor.series, step.at);
    if (!reading) return { text: 'Wind unavailable', arrow: null };
    const prefix = cursor.status === 'stale' ? '~' : '';
    return {
      text: `${cursor.place} · ${prefix}${Math.round(reading.speedKmh)} km/h ${compass16(reading.directionDeg)}`,
      arrow: reading.directionDeg + 180,
    };
  }, [cursor, step]);

  return (
    <section className="rounded-2xl border border-white/10 bg-slate-900/60 p-4 shadow-2xl backdrop-blur-xl sm:p-6">
      <header className="mb-5">
        <h2 className="flex items-center gap-2 font-display text-xl text-textPrimary sm:text-2xl">
          <Satellite className="h-5 w-5 text-accent" />
          Live Cloud View
        </h2>
        <p className="mt-1 font-sans text-sm text-textMuted">
          NASA GIBS imagery with the DWD ICON wind field on one map. The timeline steps through the last{' '}
          {(GEO_FRAMES * STEP_MINUTES) / 60} hours of satellite frames every {STEP_MINUTES} minutes, then the wind
          forecast hourly to {FORECAST_HOURS} hours ahead. Hover the map for the wind speed at that point.
        </p>
      </header>

      <div className="mb-4 flex flex-wrap items-end gap-3">
        <label className="flex items-center gap-2 rounded-lg border border-white/10 bg-black/30 px-3 py-2.5 font-mono text-xs text-textPrimary">
          <input
            type="checkbox"
            checked={showClouds}
            onChange={(e) => setShowClouds(e.target.checked)}
            className="h-4 w-4 accent-sky-400"
          />
          Clouds
        </label>
        <label className="flex w-full flex-col gap-1.5 sm:w-40">
          <span className="font-mono text-[10px] uppercase tracking-widest text-textMuted">
            Cloud opacity - {Math.round(cloudOpacity * 100)}%
          </span>
          <input
            type="range"
            min={0}
            max={1}
            step={0.05}
            value={cloudOpacity}
            onChange={(e) => setCloudOpacity(Number(e.target.value))}
            className="accent-sky-400"
          />
        </label>

        <label className="flex items-center gap-2 rounded-lg border border-white/10 bg-black/30 px-3 py-2.5 font-mono text-xs text-textPrimary">
          <input
            type="checkbox"
            checked={showWind}
            onChange={(e) => setShowWind(e.target.checked)}
            className="h-4 w-4 accent-sky-400"
          />
          Wind speed
        </label>
        <label className="flex w-full flex-col gap-1.5 sm:w-40">
          <span className="font-mono text-[10px] uppercase tracking-widest text-textMuted">
            Wind opacity - {Math.round(windOpacity * 100)}%
          </span>
          <input
            type="range"
            min={0}
            max={1}
            step={0.05}
            value={windOpacity}
            onChange={(e) => setWindOpacity(Number(e.target.value))}
            className="accent-sky-400"
          />
        </label>

        <label className="flex items-center gap-2 rounded-lg border border-white/10 bg-black/30 px-3 py-2.5 font-mono text-xs text-textPrimary">
          <input
            type="checkbox"
            checked={showTrueColor}
            onChange={(e) => setShowTrueColor(e.target.checked)}
            className="h-4 w-4 accent-sky-400"
          />
          True Color ({trueColorDate})
        </label>
      </div>

      <div className="grid grid-cols-1 gap-4 lg:grid-cols-[1fr_17rem]">
        <div>
          <div className="relative">
            {/* Lenis drives page scrolling globally; without this the wheel
                gesture scrolls the page instead of zooming the map. */}
            <div
              ref={containerRef}
              data-lenis-prevent
              style={{ backgroundColor: '#0b1220' }}
              className="relative z-0 h-[320px] w-full overflow-hidden rounded-xl border border-white/10 sm:h-[440px]"
            />

            {/* Cursor wind pill. Flips below the pointer near the top edge. */}
            {cursor && cursorLabel && (
              <div
                className="wind-cursor pointer-events-none absolute z-[500] flex items-center gap-1.5 rounded bg-black/75 px-2 py-1 text-[12px] leading-none text-white"
                style={{
                  left: cursor.x,
                  top: cursor.y + (cursor.y < 34 ? 18 : -26),
                  transform: 'translateX(-50%)',
                }}
              >
                {cursorLabel.arrow !== null && (
                  <span
                    className="inline-block"
                    style={{ transform: `rotate(${cursorLabel.arrow}deg)` }}
                    aria-hidden
                  >
                    ↑
                  </span>
                )}
                {cursorLabel.text}
              </div>
            )}

            {/* Corner overlay - never blocks the map. */}
            <div
              className={`pointer-events-none absolute bottom-3 left-3 z-[400] flex items-center gap-2 rounded-lg border border-white/10 bg-black/70 px-3 py-2 font-mono text-[11px] text-textPrimary backdrop-blur-sm transition-opacity duration-200 ${
                busy ? 'opacity-100' : 'opacity-0'
              }`}
            >
              <Loader2 className="h-3.5 w-3.5 animate-spin text-accent" />
              {slow ? 'NASA imagery is slow, still loading...' : 'Loading NASA imagery...'}
            </div>

            {(unavailable || timelineError) && (
              <div className="pointer-events-none absolute top-3 left-3 z-[400] flex items-center gap-2 rounded-lg border border-alertRed/40 bg-black/75 px-3 py-2 font-mono text-[11px] text-alertRed backdrop-blur-sm">
                <AlertCircle className="h-3.5 w-3.5" />
                {timelineError ? 'Wind forecast unavailable' : 'Satellite imagery temporarily unavailable'}
              </div>
            )}

            {isFuture && (
              <div className="pointer-events-none absolute top-3 right-3 z-[400] rounded-lg border border-white/10 bg-black/75 px-3 py-2 font-mono text-[11px] text-textMuted backdrop-blur-sm">
                Satellite imagery is available only up to now.
              </div>
            )}
          </div>

          <div className="mt-3 flex flex-wrap items-center gap-3">
            <button
              onClick={() => setPlaying((p) => !p)}
              disabled={steps.length === 0}
              className="flex items-center gap-2 rounded-lg border border-accent/30 bg-accent/10 px-3 py-2 font-mono text-xs uppercase tracking-widest text-accent transition-colors hover:bg-accent/20 disabled:opacity-50"
            >
              {playing ? <Pause className="h-4 w-4" /> : <Play className="h-4 w-4" />}
              {playing ? 'Pause' : 'Play'}
            </button>

            <input
              type="range"
              min={0}
              max={Math.max(steps.length - 1, 0)}
              step={1}
              value={stepIndex}
              disabled={steps.length === 0}
              onChange={(e) => {
                setPlaying(false);
                setStepIndex(Number(e.target.value));
              }}
              className="min-w-[8rem] flex-1 accent-sky-400"
            />

            <span className="font-mono text-xs text-textPrimary">
              {step ? `${IST_TIME.format(step.at)} IST` : '--'}
            </span>
          </div>

          <WindLegend />

          <p className="mt-2 font-mono text-[11px] text-textMuted">
            Satellite frames reach GIBS roughly {GEO_LAG_MINUTES} minutes after capture, so the past is recent
            rather than live. Wind is the DWD ICON global forecast.
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
