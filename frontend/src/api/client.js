const DEV_DEFAULT_API_BASE = "http://127.0.0.1:8000";

function normalizeBaseUrl(raw) {
  const value = String(raw || "").trim();
  if (!value) return "";
  return value.replace(/\/+$/, "");
}

export function getApiBaseUrl() {
  const configured = normalizeBaseUrl(import.meta.env.VITE_API_BASE_URL);
  if (configured) return configured;
  if (import.meta.env.DEV) return DEV_DEFAULT_API_BASE;
  throw new Error(
    "Frontend API is not configured. Set VITE_API_BASE_URL to the public backend URL (for example https://your-backend.example.com) before building for production."
  );
}

export function apiUrl(path) {
  const base = getApiBaseUrl();
  const suffix = String(path || "");
  const normalizedPath = suffix.startsWith("/") ? suffix : `/${suffix}`;
  return `${base}${normalizedPath}`;
}

export async function apiFetch(path, init) {
  const url = apiUrl(path);
  try {
    return await fetch(url, init);
  } catch (e) {
    const message = e instanceof Error ? e.message : String(e);
    throw new Error(
      `Cannot reach backend API (${url}). ${message}. Check VITE_API_BASE_URL, backend availability, and CORS_ORIGINS.`
    );
  }
}
