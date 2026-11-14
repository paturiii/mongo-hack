export type Action = { type: "link" | "replan" | "notice"; url?: string; instruction?: string };
export type Disruption = {
  _id: string; kind: string; status: string; severity: number; fixture?: boolean;
  evidence: { source_url: string; source_type: string; summary: string; fetched_at: string }[];
  affected: { days: string[]; slot_ids: string[]; link_ids: string[] };
  options: { id: string; label: string; actions: Action[] }[];
};
export type Alert = {
  _id: string; disruption_id: string; channel: string; title: string; body: string;
  status: string; audio_url?: string; delivery_mode?: string; last_error?: string;
  source_url?: string; source_type?: string; fetched_at?: string;
};
export type Snapshot = {
  board: { _id: string; trip_id: string; title: string; summary: string; share_slug: string;
    fixture?: boolean; fixture_replan?: boolean; version: number;
    days: { date: string; theme: string; slots: { slot_id: string; time: string;
      item: { name: string; why: string; source_url?: string } }[] }[];
  }; disruptions: Disruption[]; alerts: Alert[];
};
