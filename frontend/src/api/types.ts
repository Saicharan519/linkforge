// Shapes of API responses (docs/openapi.skeleton.yaml). Timestamps are UTC ISO strings.
// Daily analytics buckets are "YYYY-MM-DD" UTC calendar dates and are never turned into Dates.

export interface User {
  id: string;
  email: string;
  created_at: string;
}

export type LinkStatus = "active" | "disabled" | "expired";

export interface Link {
  id: string;
  code: string;
  /** The full short URL from the API. Always copy this; never rebuild it in the browser. */
  short_url: string;
  destination_url: string;
  title: string | null;
  is_active: boolean;
  expires_at: string | null;
  created_at: string;
  updated_at: string;
  status: LinkStatus;
  total_clicks: number;
}

export interface LinkList {
  items: Link[];
  total: number;
  page: number;
  page_size: number;
}

export interface LinkCreateBody {
  destination_url: string;
  alias?: string;
  title?: string;
  expires_at?: string;
}

/** Omitted = unchanged. null = clear (title, expires_at only). */
export interface LinkPatchBody {
  destination_url?: string;
  is_active?: boolean;
  title?: string | null;
  expires_at?: string | null;
}

export type RangeName = "7d" | "30d" | "90d";

export interface DayCount {
  day: string;
  clicks: number;
}
export interface ReferrerCount {
  host: string;
  clicks: number;
}
export interface DeviceCount {
  device_type: "desktop" | "mobile" | "tablet" | "other";
  clicks: number;
}
export interface BrowserCount {
  browser: string;
  clicks: number;
}

export interface LinkAnalytics {
  total: number;
  series: DayCount[];
  referrers: ReferrerCount[];
  devices: DeviceCount[];
  browsers: BrowserCount[];
}

export interface TopLink {
  id: string;
  code: string;
  title: string | null;
  clicks: number;
}

export interface Overview {
  total_links: number;
  total_clicks: number;
  series: DayCount[];
  top_links: TopLink[];
}

export interface ApiKey {
  id: string;
  name: string;
  prefix: string;
  created_at: string;
  last_used_at: string | null;
  revoked_at: string | null;
}

/** Only the create response carries the plaintext key, once. */
export interface ApiKeyCreated extends ApiKey {
  key: string;
}
