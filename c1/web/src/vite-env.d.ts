/// <reference types="vite/client" />

interface ImportMetaEnv {
  readonly VITE_C2_BASE?: string;
  readonly VITE_C3_BASE?: string;
  readonly VITE_API_BASE?: string;
}

interface ImportMeta {
  readonly env: ImportMetaEnv;
}
