export type InputMode = 'voice' | 'text';
export type RoutingPath = 'fast' | 'slow';

export interface QueryRequest {
  text: string;
  input_mode: InputMode;
}

export interface QueryResponse {
  answer: string;
  path: RoutingPath;
  verified: boolean;
  source_data: Record<string, unknown>;
  latency_ms: number;
}

export interface QueryError {
  error: string;
  answer: null;
}

// Shape of POST /query as the backend actually returns it (see
// backend/models/schemas.py:FinalAnswer). Success sets `answer` and
// leaves `error` null; failure does the exact opposite - never both.
interface FinalAnswer {
  answer: string | null;
  path: RoutingPath;
  verified: boolean;
  source_data: Record<string, unknown> | null;
  latency_ms: number;
  error: string | null;
}

// Empty by default: the Vite dev server proxies /query and /voice/* to
// the backend (see vite.config.ts), and in production the backend serves
// this build itself, so same-origin requests work in both cases. Set
// VITE_API_BASE only when the API lives on a different origin.
export const API_BASE = (import.meta.env.VITE_API_BASE ?? '').replace(/\/$/, '');

export async function fetchWeatherQuery(req: QueryRequest): Promise<QueryResponse> {
  let res: Response;
  try {
    res = await fetch(`${API_BASE}/query`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(req),
    });
  } catch {
    // Fail loud: the UI must never show a silent blank state.
    throw {
      error: 'Could not reach the WeatherGPT backend. Is it running on port 8000?',
      answer: null,
    } as QueryError;
  }

  let data: Partial<FinalAnswer> | null = null;
  try {
    data = (await res.json()) as Partial<FinalAnswer>;
  } catch {
    data = null;
  }

  if (!res.ok) {
    throw {
      error: data?.error ?? `Backend returned HTTP ${res.status} ${res.statusText}.`,
      answer: null,
    } as QueryError;
  }

  if (!data || data.error || !data.answer) {
    throw {
      error: data?.error ?? 'The backend returned no answer.',
      answer: null,
    } as QueryError;
  }

  return {
    answer: data.answer,
    path: data.path ?? 'fast',
    verified: Boolean(data.verified),
    source_data: data.source_data ?? {},
    latency_ms: data.latency_ms ?? 0,
  };
}
