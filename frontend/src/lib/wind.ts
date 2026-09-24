// Cursor wind lookup for the Live Cloud View map.
//
// Values come from the Open-Meteo forecast API rather than from the map layer,
// so the readout still works when the wind layer is switched off. The request
// pins models=icon_global so the number under the cursor agrees with the DWD
// ICON field the map paints.

const ENDPOINT = 'https://api.open-meteo.com/v1/forecast';

/** Results are shared across a 0.25 degree cell - roughly 25 km, finer than
 *  the ICON global grid, so snapping costs no visible accuracy. */
const CELL_DEG = 0.25;
const CACHE_TTL_MS = 10 * 60_000;
const MAX_REQUESTS_PER_SECOND = 4;
const CITY_SNAP_KM = 40;
/** Asia/Kolkata is a fixed +5:30 with no DST, so a constant offset is safe. */
const IST_OFFSET_MS = 5.5 * 3600_000;

export interface WindSeries {
  /** Local IST hourly stamps, "YYYY-MM-DDTHH:mm". */
  time: string[];
  speedKmh: (number | null)[];
  directionDeg: (number | null)[];
}

export interface WindCity {
  name: string;
  latitude: number;
  longitude: number;
}

export const WIND_CITIES: WindCity[] = [
  { name: 'Bhopal', latitude: 23.2599, longitude: 77.4126 },
  { name: 'Indore', latitude: 22.7196, longitude: 75.8577 },
  { name: 'Delhi', latitude: 28.6139, longitude: 77.209 },
  { name: 'Mumbai', latitude: 19.076, longitude: 72.8777 },
  { name: 'Chennai', latitude: 13.0827, longitude: 80.2707 },
  { name: 'Kolkata', latitude: 22.5726, longitude: 88.3639 },
  { name: 'Bengaluru', latitude: 12.9716, longitude: 77.5946 },
  { name: 'Hyderabad', latitude: 17.385, longitude: 78.4867 },
  { name: 'Ahmedabad', latitude: 23.0225, longitude: 72.5714 },
  { name: 'Pune', latitude: 18.5204, longitude: 73.8567 },
  { name: 'Jaipur', latitude: 26.9124, longitude: 75.7873 },
  { name: 'Lucknow', latitude: 26.8467, longitude: 80.9462 },
  { name: 'Nagpur', latitude: 21.1458, longitude: 79.0882 },
];

const COMPASS = [
  'N', 'NNE', 'NE', 'ENE', 'E', 'ESE', 'SE', 'SSE',
  'S', 'SSW', 'SW', 'WSW', 'W', 'WNW', 'NW', 'NNW',
];

/** 16-point compass label for the direction the wind blows *from*. */
export function compass16(deg: number): string {
  const norm = ((deg % 360) + 360) % 360;
  return COMPASS[Math.round(norm / 22.5) % 16];
}

function haversineKm(aLat: number, aLon: number, bLat: number, bLon: number): number {
  const toRad = Math.PI / 180;
  const dLat = (bLat - aLat) * toRad;
  const dLon = (bLon - aLon) * toRad;
  const s =
    Math.sin(dLat / 2) ** 2 +
    Math.cos(aLat * toRad) * Math.cos(bLat * toRad) * Math.sin(dLon / 2) ** 2;
  return 6371 * 2 * Math.asin(Math.sqrt(s));
}

/** The city within CITY_SNAP_KM of the point, if any. */
export function nearestCity(lat: number, lon: number): WindCity | null {
  let best: WindCity | null = null;
  let bestKm = CITY_SNAP_KM;
  for (const city of WIND_CITIES) {
    const km = haversineKm(lat, lon, city.latitude, city.longitude);
    if (km <= bestKm) {
      best = city;
      bestKm = km;
    }
  }
  return best;
}

function snap(value: number): number {
  return Math.round(value / CELL_DEG) * CELL_DEG;
}

export function cellKey(lat: number, lon: number): string {
  return `${snap(lat).toFixed(2)},${snap(lon).toFixed(2)}`;
}

interface CacheEntry {
  at: number;
  lat: number;
  lon: number;
  series: WindSeries;
}

const cache = new Map<string, CacheEntry>();
let recentRequests: number[] = [];

function fresh(entry: CacheEntry | undefined): entry is CacheEntry {
  return !!entry && Date.now() - entry.at < CACHE_TTL_MS;
}

/** Cached series for exactly this cell, if still within its TTL. */
export function cachedSeries(lat: number, lon: number): WindSeries | null {
  const entry = cache.get(cellKey(lat, lon));
  return fresh(entry) ? entry.series : null;
}

/** Closest cached series regardless of cell, used as the degraded fallback. */
export function nearestCachedSeries(lat: number, lon: number): WindSeries | null {
  let best: WindSeries | null = null;
  let bestKm = Infinity;
  for (const entry of cache.values()) {
    if (!fresh(entry)) continue;
    const km = haversineKm(lat, lon, entry.lat, entry.lon);
    if (km < bestKm) {
      bestKm = km;
      best = entry.series;
    }
  }
  return best;
}

/** False when four requests already went out in the last second. */
export function underRateLimit(): boolean {
  const now = Date.now();
  recentRequests = recentRequests.filter((t) => now - t < 1000);
  return recentRequests.length < MAX_REQUESTS_PER_SECOND;
}

export async function loadWindSeries(
  lat: number,
  lon: number,
  signal: AbortSignal,
): Promise<WindSeries> {
  const key = cellKey(lat, lon);
  const hit = cache.get(key);
  if (fresh(hit)) return hit.series;

  const cellLat = snap(lat);
  const cellLon = snap(lon);
  const url =
    `${ENDPOINT}?latitude=${cellLat}&longitude=${cellLon}` +
    `&hourly=wind_speed_10m,wind_direction_10m&models=icon_global` +
    `&wind_speed_unit=kmh&timezone=Asia%2FKolkata&forecast_days=3`;

  recentRequests.push(Date.now());
  const res = await fetch(url, { signal });
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  const body = (await res.json()) as {
    hourly?: {
      time?: string[];
      wind_speed_10m?: (number | null)[];
      wind_direction_10m?: (number | null)[];
    };
  };

  const series: WindSeries = {
    time: body.hourly?.time ?? [],
    speedKmh: body.hourly?.wind_speed_10m ?? [],
    directionDeg: body.hourly?.wind_direction_10m ?? [],
  };
  if (!series.time.length) throw new Error('empty series');

  cache.set(key, { at: Date.now(), lat: cellLat, lon: cellLon, series });
  return series;
}

/** The "YYYY-MM-DDTHH:00" IST key for an instant, rounded to the nearest hour. */
export function istHourKey(at: Date): string {
  const rounded = new Date(Math.round(at.getTime() / 3600_000) * 3600_000);
  return `${new Date(rounded.getTime() + IST_OFFSET_MS).toISOString().slice(0, 13)}:00`;
}

export interface WindReading {
  speedKmh: number;
  directionDeg: number;
}

/** Value at the timeline's current hour, or the nearest hour the series has. */
export function readingAt(series: WindSeries, at: Date): WindReading | null {
  let i = series.time.indexOf(istHourKey(at));
  if (i === -1) {
    // Outside the returned window - fall back to the closest stamp so the
    // label degrades to "near enough" rather than blanking out.
    const target = at.getTime() + IST_OFFSET_MS;
    let bestGap = Infinity;
    series.time.forEach((t, idx) => {
      const gap = Math.abs(new Date(`${t}Z`).getTime() - target);
      if (gap < bestGap) {
        bestGap = gap;
        i = idx;
      }
    });
    if (i === -1) return null;
  }
  const speed = series.speedKmh[i];
  const direction = series.directionDeg[i];
  if (typeof speed !== 'number' || typeof direction !== 'number') return null;
  return { speedKmh: speed, directionDeg: direction };
}
