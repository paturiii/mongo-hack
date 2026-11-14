// Thin client for the Wander gateway. Same-origin (/api, /auth) via Next rewrites, cookie auth.

export class ApiError extends Error {
  constructor(public status: number, message: string) {
    super(message);
  }
}

export async function api<T = unknown>(path: string, init?: {method?: string; body?: unknown}): Promise<T> {
  const res = await fetch(path, {
    method: init?.method ?? (init?.body !== undefined ? 'POST' : 'GET'),
    headers: init?.body !== undefined ? {'Content-Type': 'application/json'} : undefined,
    body: init?.body !== undefined ? JSON.stringify(init.body) : undefined,
    credentials: 'same-origin',
    cache: 'no-store',
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    const detail = typeof data?.detail === 'string' ? data.detail : 'Something went wrong. Try again.';
    throw new ApiError(res.status, detail);
  }
  return data as T;
}

export const GATEWAY = process.env.NEXT_PUBLIC_API_BASE_URL || 'http://localhost:8000';

// ---- shapes returned by the gateway (subset the UI reads)

export type Me = {id: string; email: string; name: string; plan: string; usage_today: number; quota: number};

export type QuestionItem = {
  id: string;
  prompt: string;
  type: 'single' | 'multi' | 'free' | 'date_range' | 'number';
  options: {id: string; label: string}[];
  why: string;
  required: boolean;
  hint?: string | null;
};

export type Questions = {_id: string; batch: number; items: QuestionItem[]; status: 'open' | 'answered'};

export type Message = {role: 'user' | 'assistant' | 'system'; content: string; at?: string};

export type Progress = {
  jobs_total: number;
  jobs_done: number;
  by_kind: Record<string, {done: number; total: number}>;
  opinions: number;
  sources: string[];
};

export type SessionView = {
  session: {id: string; status: string; messages: Message[]; created_at?: string};
  trip: {
    _id: string;
    destination?: {name: string; country?: string} | null;
    dates?: {start: string; end: string} | null;
    travelers?: number | null;
    status: string;
    profile?: Record<string, unknown>;
  };
  questions: Questions | null;
  progress: Progress | null;
  board: {slug: string; title: string; version: number} | null;
};

export type SessionSummary = {
  id: string;
  status: string;
  created_at: string;
  first_message: string;
  destination: string | null;
  board: {slug: string; title: string} | null;
};

export type Geo = {type: 'Point'; coordinates: [number, number]};

export type Slot = {
  slot_id: string;
  time: string;
  item: {
    place_id?: string;
    name: string;
    why?: string;
    geo?: Geo | null;
    category?: string;
    opinion_chips?: {text: string; source_type: string; url: string}[];
    link_ids?: string[];
    est_cost?: {amount: number; currency: string} | null;
  };
};

export type Booking = {link_id: string; kind: string; label: string; url: string; status: 'todo' | 'user_marked_booked'; deadline_hint?: string | null};

export type Link = {_id: string; kind: string; provider: string; url: string; label: string; is_official?: boolean};

// Disruption + alert shapes are owned by Track 1 (components/wander/types.ts).
import type {Alert, Disruption} from '@/components/wander/types';
export type {Alert, Disruption};

export type BoardView = {
  board: {
    _id: string;
    trip_id: string;
    user_id: string;
    version: number;
    share_slug: string;
    title: string;
    summary?: string;
    checklist_progress?: {booked: number; total: number};
    days: {date: string; theme?: string; slots: Slot[]}[];
    bookings: Booking[];
    updated_at?: string;
  };
  links: Record<string, Link>;
  alerts: Alert[];
  disruptions: Disruption[];
  trip: {destination?: {name: string}; dates?: {start: string; end: string}; travelers?: number; user_id?: string} | null;
};

export type Memory = {
  _id: string;
  kind: 'pref' | 'episode' | 'feedback';
  key?: string | null;
  value?: unknown;
  text?: string | null;
  source?: string;
  updated_at: string;
};
