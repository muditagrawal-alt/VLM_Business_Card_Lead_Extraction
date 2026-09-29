/// <reference types="vite/client" />

interface ImportMetaEnv {
  /**
   * The API's origin when the SPA is served from somewhere else, such as
   * https://api.example.com. Unset when Caddy serves both from one origin.
   */
  readonly VITE_API_BASE_URL?: string;
}

interface ImportMeta {
  readonly env: ImportMetaEnv;
}
