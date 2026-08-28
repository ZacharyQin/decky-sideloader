export type PluginRow = {
  name: string;
  folder: string;
  version: string | null;
  author: string;
  repo: string | null;
  is_self: boolean;
  in_store: boolean | null;
  sideload: boolean;
};

export type PluginListResponse = {
  plugins: PluginRow[];
  store_ok: boolean;
  store_error: string | null;
};
