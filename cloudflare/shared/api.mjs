import { clamp, nowIso, parseFloatSafe, parseIntSafe } from "./helpers.mjs";
import { runHealthCheck, runScanner } from "./scanner.mjs";

const SCAN_CACHE = new Map();

function jsonResponse(status, payload, corsOrigin = "*") {
  return new Response(JSON.stringify(payload), {
    status,
    headers: {
      "content-type": "application/json; charset=utf-8",
      "cache-control": "no-store",
      "access-control-allow-origin": corsOrigin,
      "access-control-allow-methods": "GET, OPTIONS",
      "access-control-allow-headers": "content-type",
    },
  });
}

function corsOnlyResponse(corsOrigin = "*") {
  return new Response(null, {
    status: 204,
    headers: {
      "access-control-allow-origin": corsOrigin,
      "access-control-allow-methods": "GET, OPTIONS",
      "access-control-allow-headers": "content-type",
    },
  });
}

function readString(search, key, defaultValue = "") {
  const value = search.get(key);
  if (value === null) {
    return defaultValue;
  }
  return String(value).trim();
}

function readFloat(search, key, defaultValue) {
  const value = search.get(key);
  if (value === null || value === "") {
    return defaultValue;
  }
  const parsed = parseFloatSafe(value, Number.NaN);
  return Number.isFinite(parsed) ? parsed : defaultValue;
}

function readInt(search, key, defaultValue) {
  const value = search.get(key);
  if (value === null || value === "") {
    return defaultValue;
  }
  const parsed = parseIntSafe(value, Number.NaN);
  return Number.isFinite(parsed) ? parsed : defaultValue;
}

function readBool(search, key, defaultValue = false) {
  const value = search.get(key);
  if (value === null || value === "") {
    return defaultValue;
  }
  const normalized = String(value).trim().toLowerCase();
  if (["1", "true", "yes", "on"].includes(normalized)) {
    return true;
  }
  if (["0", "false", "no", "off"].includes(normalized)) {
    return false;
  }
  return defaultValue;
}

function validateParamRanges(params) {
  if (params.bankroll <= 0 || params.bankroll > 100000000) {
    return "Invalid bankroll";
  }
  if (params.min_margin < 0 || params.min_margin > 100) {
    return "Invalid min_margin";
  }
  if (params.min_data_quality < 0 || params.min_data_quality > 1) {
    return "Invalid min_data_quality";
  }
  if (params.min_source_quorum < 1 || params.min_source_quorum > 5) {
    return "Invalid min_source_quorum";
  }
  if (params.slippage_bps < 0 || params.slippage_bps > 2000) {
    return "Invalid slippage_bps";
  }
  if (params.rejection_rate < 0 || params.rejection_rate > 1) {
    return "Invalid rejection_rate";
  }
  if (params.max_events < 1 || params.max_events > 120) {
    return "Invalid max_events";
  }
  if (params.stale_threshold_sec < 10 || params.stale_threshold_sec > 86400) {
    return "Invalid stale_threshold_sec";
  }
  if (params.sample_check < 0 || params.sample_check > 50) {
    return "Invalid sample_check";
  }
  if (!["flashscore", "soccer24", "livesport", "betexplorer", "sofascore", "multi"].includes(params.scraper_source)) {
    return "Invalid scraper_source";
  }
  if (!/^[A-Z]{2,10}$/.test(params.geo_ip_code)) {
    return "Invalid geo_ip_code";
  }
  if (!/^[A-Z0-9]{2,10}$/.test(params.geo_ip_subdivision)) {
    return "Invalid geo_ip_subdivision";
  }
  if (params.max_bookmakers_per_event < 1 || params.max_bookmakers_per_event > 30) {
    return "Invalid max_bookmakers_per_event";
  }
  if (params.quorum_min_sources < 1 || params.quorum_min_sources > 5) {
    return "Invalid quorum_min_sources";
  }
  if (!/^[a-z]{2,5}$/i.test(params.betexplorer_lang)) {
    return "Invalid betexplorer_lang";
  }
  return null;
}

function parseScanParams(request, env) {
  const url = new URL(request.url);
  const search = url.searchParams;

  const params = {
    bankroll: readFloat(search, "bankroll", 1000),
    min_margin: readFloat(search, "min_margin", 0),
    min_data_quality: readFloat(search, "min_data_quality", 0),
    min_source_quorum: readInt(search, "min_source_quorum", 1),
    slippage_bps: readFloat(search, "slippage_bps", 0),
    rejection_rate: readFloat(search, "rejection_rate", 0),
    max_events: readInt(search, "max_events", 25),
    stale_threshold_sec: readInt(search, "stale_threshold_sec", 900),
    sample_check: readInt(search, "sample_check", 5),
    include_prematch: readString(search, "include_prematch", "0") === "1",
    scraper_source: readString(search, "scraper_source", "flashscore"),
    include_flashscore: readBool(search, "include_flashscore", true),
    include_soccer24: readBool(search, "include_soccer24", true),
    include_livesport: readBool(search, "include_livesport", true),
    include_betexplorer: readBool(search, "include_betexplorer", true),
    include_sofascore: readBool(search, "include_sofascore", false),
    geo_ip_code: readString(search, "geo_ip_code", "GB").toUpperCase(),
    geo_ip_subdivision: readString(search, "geo_ip_subdivision", "GBENG").toUpperCase(),
    max_bookmakers_per_event: readInt(search, "max_bookmakers_per_event", 8),
    quorum_min_sources: readInt(search, "quorum_min_sources", 2),
    betexplorer_lang: readString(search, "betexplorer_lang", "en"),
    refresh: readString(search, "refresh", "0") === "1",
    limits_url: readString(search, "limits_url", ""),
    timeout_ms: Math.max(5000, parseIntSafe(env?.SCRAPER_TIMEOUT_MS, 20000)),
  };

  return params;
}

function makeCacheKey(params) {
  return JSON.stringify({
    bankroll: params.bankroll,
    min_margin: params.min_margin,
    min_data_quality: params.min_data_quality,
    min_source_quorum: params.min_source_quorum,
    slippage_bps: params.slippage_bps,
    rejection_rate: params.rejection_rate,
    max_events: params.max_events,
    stale_threshold_sec: params.stale_threshold_sec,
    sample_check: params.sample_check,
    include_prematch: params.include_prematch,
    scraper_source: params.scraper_source,
    include_flashscore: params.include_flashscore,
    include_soccer24: params.include_soccer24,
    include_livesport: params.include_livesport,
    include_betexplorer: params.include_betexplorer,
    include_sofascore: params.include_sofascore,
    geo_ip_code: params.geo_ip_code,
    geo_ip_subdivision: params.geo_ip_subdivision,
    max_bookmakers_per_event: params.max_bookmakers_per_event,
    quorum_min_sources: params.quorum_min_sources,
    betexplorer_lang: params.betexplorer_lang,
    limits_url: params.limits_url,
  });
}

async function loadBookmakerLimits(limitsUrl, timeoutMs) {
  if (!limitsUrl) {
    return {};
  }
  let url;
  try {
    url = new URL(limitsUrl);
  } catch {
    throw new Error("limits_url must be a valid absolute URL");
  }
  if (!["https:", "http:"].includes(url.protocol)) {
    throw new Error("limits_url must start with https:// or http://");
  }

  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort("timeout"), timeoutMs);
  try {
    const res = await fetch(url.toString(), {
      method: "GET",
      headers: {
        Accept: "application/json",
        "User-Agent": "iddiaarb-cloudflare/1.0",
      },
      signal: controller.signal,
    });
    if (!res.ok) {
      throw new Error(`limits_url HTTP ${res.status}`);
    }
    const payload = await res.json();
    const table = payload?.bookmakers && typeof payload.bookmakers === "object" ? payload.bookmakers : payload;
    if (!table || typeof table !== "object") {
      throw new Error("Invalid limits payload. Expected object or {bookmakers:{...}}.");
    }

    const out = {};
    for (const [bookmaker, row] of Object.entries(table)) {
      if (!row || typeof row !== "object") {
        continue;
      }
      out[String(bookmaker)] = {
        min_stake: Number(row.min_stake || 0),
        max_stake: row.max_stake === null || row.max_stake === undefined ? null : Number(row.max_stake),
        max_payout: row.max_payout === null || row.max_payout === undefined ? null : Number(row.max_payout),
        commission_pct: Number(row.commission_pct || 0),
      };
    }
    return out;
  } finally {
    clearTimeout(timer);
  }
}

function cleanupCache(nowMs) {
  for (const [key, value] of SCAN_CACHE.entries()) {
    if (!value || typeof value.expires_at !== "number" || value.expires_at <= nowMs) {
      SCAN_CACHE.delete(key);
    }
  }
}

export async function handleScanRequest(request, env) {
  const corsOrigin = env?.CORS_ORIGIN || "*";
  const params = parseScanParams(request, env);
  const validationError = validateParamRanges(params);
  if (validationError) {
    return jsonResponse(400, { ok: false, error: validationError }, corsOrigin);
  }

  const cacheTtlSec = clamp(parseIntSafe(env?.CACHE_TTL_SECONDS, 20), 5, 300);
  const nowMs = Date.now();
  const cacheKey = makeCacheKey(params);

  cleanupCache(nowMs);
  if (!params.refresh) {
    const cached = SCAN_CACHE.get(cacheKey);
    if (cached && cached.expires_at > nowMs) {
      return jsonResponse(
        200,
        {
          ...cached.payload,
          cached: true,
          generated_at: cached.payload.generated_at || nowIso(),
        },
        corsOrigin,
      );
    }
  }

  try {
    const bookmakerLimits = await loadBookmakerLimits(params.limits_url, params.timeout_ms);
    const scan = await runScanner({ ...params, bookmaker_limits: bookmakerLimits });

    const payload = {
      ok: true,
      cached: false,
      generated_at: nowIso(),
      params: {
        bankroll: params.bankroll,
        min_margin: params.min_margin,
        min_data_quality: params.min_data_quality,
        min_source_quorum: params.min_source_quorum,
        slippage_bps: params.slippage_bps,
        rejection_rate: params.rejection_rate,
        max_events: params.max_events,
        stale_threshold_sec: params.stale_threshold_sec,
        sample_check: params.sample_check,
        include_prematch: params.include_prematch,
        scraper_source: params.scraper_source,
        include_flashscore: params.include_flashscore,
        include_soccer24: params.include_soccer24,
        include_livesport: params.include_livesport,
        include_betexplorer: params.include_betexplorer,
        include_sofascore: params.include_sofascore,
        geo_ip_code: params.geo_ip_code,
        geo_ip_subdivision: params.geo_ip_subdivision,
        max_bookmakers_per_event: params.max_bookmakers_per_event,
        quorum_min_sources: params.quorum_min_sources,
        betexplorer_lang: params.betexplorer_lang,
        limits_url: params.limits_url,
      },
      scan,
    };

    SCAN_CACHE.set(cacheKey, {
      expires_at: nowMs + cacheTtlSec * 1000,
      payload,
    });

    return jsonResponse(200, payload, corsOrigin);
  } catch (error) {
    return jsonResponse(
      500,
      {
        ok: false,
        error: String(error?.message || error),
      },
      corsOrigin,
    );
  }
}

export async function handleHealthRequest(request, env) {
  const corsOrigin = env?.CORS_ORIGIN || "*";
  const url = new URL(request.url);
  const checkScraper = url.searchParams.get("check_scraper") === "1";

  try {
    const payload = await runHealthCheck({ checkScraper });
    const statusCode = payload.status === "ok" ? 200 : 503;
    return jsonResponse(statusCode, payload, corsOrigin);
  } catch (error) {
    return jsonResponse(
      500,
      {
        status: "error",
        timestamp: nowIso(),
        runtime: "cloudflare-worker",
        error: String(error?.message || error),
      },
      corsOrigin,
    );
  }
}

export function handleOptions(env) {
  const corsOrigin = env?.CORS_ORIGIN || "*";
  return corsOnlyResponse(corsOrigin);
}

