// Client for the researcher-dashboard endpoints. Same same-origin
// convention as lib/api.ts - no base URL to configure.
import { API_BASE } from './api';

export interface ModelInfo {
  id: string;
  label: string;
  centre: string;
}

export interface ParameterInfo {
  id: string;
  label: string;
  unit: string;
}

export interface ResearchCatalog {
  models: ModelInfo[];
  default: string[];
  parameters: ParameterInfo[];
  historical_parameters: { id: string; label: string; unit: string }[];
}

export interface ModelSeries {
  model: string;
  label: string;
  values: (number | null)[];
}

export interface ComparedParameter {
  id: string;
  label: string;
  unit: string;
  series: ModelSeries[];
  spread: number | null;
}

export interface ModelComparison {
  location: string;
  latitude: number;
  longitude: number;
  time: string[];
  models: ModelInfo[];
  parameters: ComparedParameter[];
  forecast_days: number;
}

export interface HistoricalTrend {
  location: string;
  parameter: string;
  label: string;
  unit: string;
  aggregation: 'daily' | 'monthly' | 'yearly';
  start_date: string;
  end_date: string;
  labels: string[];
  values: (number | null)[];
  rolling_average: (number | null)[];
  rolling_window: number;
  stats: { mean: number | null; min: number | null; max: number | null; count: number };
  observations: number;
}

export interface RegionalPoint {
  name: string;
  region: 'arabian_sea' | 'bay_of_bengal' | 'west_coast' | 'east_coast';
  latitude: number;
  longitude: number;
  current: Record<string, number | string | null>;
  daily: Record<string, (number | string | null)[]>;
}

export interface RegionalContext {
  sea_points: RegionalPoint[];
  coastal_points: RegionalPoint[];
  lowest_pressure: { name: string; value: number; unit: string } | null;
  strongest_gust: { name: string; value: number; unit: string } | null;
  fetched_points: number;
}

export interface RegionalNarrative {
  narrative: string | null;
  verified: boolean;
  disclaimer?: string;
  error?: string;
  mismatches?: string[];
  latency_ms: number;
}

async function getJson<T>(path: string): Promise<T> {
  let res: Response;
  try {
    res = await fetch(`${API_BASE}${path}`);
  } catch {
    throw new Error('Could not reach the WeatherGPT backend. Is it running on port 8000?');
  }
  if (!res.ok) {
    // FastAPI puts validation and provider errors in `detail`.
    let detail: string | undefined;
    try {
      detail = ((await res.json()) as { detail?: string }).detail;
    } catch {
      detail = undefined;
    }
    throw new Error(detail ?? `Request failed with HTTP ${res.status}`);
  }
  return (await res.json()) as T;
}

export const fetchCatalog = () => getJson<ResearchCatalog>('/research/models');

export const fetchModelComparison = (location: string, models: string[], forecastDays: number) =>
  getJson<ModelComparison>(
    `/research/compare-models?location=${encodeURIComponent(location)}` +
      `&models=${encodeURIComponent(models.join(','))}&forecast_days=${forecastDays}`,
  );

export const fetchHistoricalTrend = (
  location: string,
  parameter: string,
  startDate: string,
  endDate: string,
  aggregation: string,
) =>
  getJson<HistoricalTrend>(
    `/research/historical-trend?location=${encodeURIComponent(location)}` +
      `&parameter=${encodeURIComponent(parameter)}&start_date=${startDate}` +
      `&end_date=${endDate}&aggregation=${aggregation}`,
  );

export const fetchRegionalContext = () => getJson<RegionalContext>('/research/regional-context');

export const fetchRegionalNarrative = () =>
  getJson<RegionalNarrative>('/research/regional-context/narrative');
