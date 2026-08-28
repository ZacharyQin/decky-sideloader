export type PluginRow = {
  name: string;
  folder: string;
  version: string | null;
  author: string;
  repo: string | null;
  repo_source: "user" | "package.json" | null;
  is_self: boolean;
  in_store: boolean | null;
  sideload: boolean;
};

export type PluginListResponse = {
  plugins: PluginRow[];
  store_ok: boolean;
  store_error: string | null;
};

export type ZipAsset = {
  name: string;
  url: string;
};

export type ReleaseRow = {
  tag: string;
  title: string;
  prerelease: boolean;
  zips: ZipAsset[];
  newer: boolean | null;
  has_zip: boolean;
};

export type UpdateInfo = {
  name: string;
  repo: string;
  current: string | null;
  latest: string;
  zip_url: string;
  zip_name: string;
};

export type AppSettings = {
  bindings: Record<string, { repo: string; overridden: boolean }>;
  autoCheck: boolean;
  autoCheckMinutes: number;
};
