export function clamp(value, min, max) {
  return Math.max(min, Math.min(max, value));
}

export function clamp01(value) {
  return clamp(value, 0, 1);
}

export function nowIso() {
  return new Date().toISOString();
}

export function normalizeText(value) {
  return String(value || "")
    .normalize("NFKD")
    .replace(/[\u0300-\u036f]/g, "")
    .toLowerCase();
}

export function normalizeTeamName(value, stopwords) {
  return normalizeText(value)
    .replace(/[^a-z0-9\s]/g, " ")
    .split(/\s+/)
    .filter((token) => token && token.length > 1 && !stopwords.has(token))
    .join(" ");
}

export function createTimeoutSignal(timeoutMs) {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort("timeout"), timeoutMs);
  return {
    signal: controller.signal,
    cancel: () => clearTimeout(timer),
  };
}

export async function fetchText(url, { headers = {}, timeoutMs = 20000 } = {}) {
  const { signal, cancel } = createTimeoutSignal(timeoutMs);
  try {
    const res = await fetch(url, { method: "GET", headers, signal });
    if (!res.ok) {
      throw new Error(`HTTP ${res.status} for ${url}`);
    }
    const text = await res.text();
    if (!text) {
      throw new Error(`Empty response from ${url}`);
    }
    return text;
  } finally {
    cancel();
  }
}

export async function fetchJson(url, { headers = {}, timeoutMs = 20000 } = {}) {
  const raw = await fetchText(url, { headers, timeoutMs });
  let payload;
  try {
    payload = JSON.parse(raw);
  } catch {
    throw new Error(`Invalid JSON from ${url}`);
  }
  if (!payload || typeof payload !== "object" || Array.isArray(payload)) {
    throw new Error(`Unexpected payload type from ${url}`);
  }
  return payload;
}

export function parseFloatSafe(value, defaultValue) {
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : defaultValue;
}

export function parseIntSafe(value, defaultValue) {
  const parsed = Number.parseInt(String(value), 10);
  return Number.isFinite(parsed) ? parsed : defaultValue;
}
