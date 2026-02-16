import { fetchJson, nowIso, parseFloatSafe, parseIntSafe } from "./helpers.mjs";

const BASE_URLS = ["https://www.sofascore.com/api/v1", "https://api.sofascore.com/api/v1"];

const SOFA_HEADERS = {
  Accept: "application/json, text/plain, */*",
  "Accept-Language": "en-US,en;q=0.9",
  "Cache-Control": "no-cache",
  Pragma: "no-cache",
  Origin: "https://www.sofascore.com",
  Referer: "https://www.sofascore.com/",
  "User-Agent":
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
};

function fractionalToDecimal(value) {
  const raw = String(value || "").trim().toUpperCase();
  if (!raw) {
    return null;
  }
  if (raw === "EVS" || raw === "EVEN" || raw === "EVENS") {
    return 2;
  }
  if (raw.includes("/")) {
    const [left, right] = raw.split("/", 2);
    const num = parseFloatSafe(left, Number.NaN);
    const den = parseFloatSafe(right, Number.NaN);
    if (!Number.isFinite(num) || !Number.isFinite(den) || den <= 0) {
      return null;
    }
    const decimal = 1 + num / den;
    return decimal > 1 ? decimal : null;
  }
  const decimal = parseFloatSafe(raw, Number.NaN);
  if (!Number.isFinite(decimal) || decimal <= 1) {
    return null;
  }
  return decimal;
}

function buildMarketQuotes(oddsPayload, includePrematch) {
  const markets = oddsPayload?.markets;
  if (!Array.isArray(markets)) {
    return { marketQuotes: [], signatures: [] };
  }

  const marketQuotes = [];
  const signatures = [];

  for (const market of markets) {
    if (!market || typeof market !== "object") {
      continue;
    }
    if (market.marketName !== "Full time") {
      continue;
    }

    const choices = market.choices;
    if (!Array.isArray(choices)) {
      continue;
    }

    const values = {};
    for (const choice of choices) {
      if (!choice || typeof choice !== "object") {
        continue;
      }
      const name = String(choice.name || "").trim().toUpperCase();
      if (!["1", "X", "2"].includes(name)) {
        continue;
      }
      const decimal = fractionalToDecimal(choice.fractionalValue || "");
      if (decimal === null) {
        continue;
      }
      values[name] = decimal;
    }

    if (!values["1"] || !values["X"] || !values["2"]) {
      continue;
    }

    const sourceId = String(market.sourceId || market.fid || market.id || "0");
    const isLive = Boolean(market.isLive);

    if (!isLive && !includePrematch) {
      continue;
    }

    marketQuotes.push({
      bookmaker: `SofaScore-${isLive ? "live" : "prematch"}-${sourceId}`,
      market_key: isLive ? "h2h_live" : "h2h_prematch",
      outcomes: [
        { key: "HOME", label: "1", odds: values["1"] },
        { key: "DRAW", label: "X", odds: values["X"] },
        { key: "AWAY", label: "2", odds: values["2"] },
      ],
      source: "sofascore",
      quality_score: isLive ? 0.6 : 0.55,
    });

    signatures.push(
      [sourceId, isLive ? "L" : "P", values["1"].toFixed(6), values["X"].toFixed(6), values["2"].toFixed(6)].join(
        "|",
      ),
    );
  }

  return {
    marketQuotes,
    signatures: signatures.sort(),
  };
}

function eventChangeAge(eventRow, nowTsSec) {
  const ts = eventRow?.changes?.changeTimestamp;
  if (typeof ts !== "number" || ts <= 0) {
    return null;
  }
  return Math.max(0, nowTsSec - ts);
}

async function fetchFromAnyBase(path, timeoutMs) {
  let lastError = null;
  for (const baseUrl of BASE_URLS) {
    try {
      const payload = await fetchJson(`${baseUrl}${path}`, {
        timeoutMs,
        headers: SOFA_HEADERS,
      });
      return { payload, baseUrl };
    } catch (error) {
      lastError = error;
    }
  }
  throw lastError || new Error(`all_sofascore_bases_failed:${path}`);
}

async function fetchLiveEvents(timeoutMs) {
  return fetchFromAnyBase("/sport/football/events/live", timeoutMs);
}

async function fetchEventOdds(eventId, timeoutMs) {
  return fetchFromAnyBase(`/event/${eventId}/odds/1/all`, timeoutMs);
}

export async function loadSofaScoreEvents(options) {
  const nowTsSec = Date.now() / 1000;
  const maxEvents = Math.max(1, parseIntSafe(options.max_events, 40));
  const staleThresholdSec = Math.max(1, parseIntSafe(options.stale_threshold_sec, 900));
  const sampleCheck = Math.max(0, parseIntSafe(options.sample_check, 5));
  const timeoutMs = Math.max(5000, parseIntSafe(options.timeout_ms, 20000));
  const includePrematch = Boolean(options.include_prematch);

  let baseUrlUsed = BASE_URLS[0];
  const liveResult = await fetchLiveEvents(timeoutMs);
  const payload = liveResult.payload;
  baseUrlUsed = liveResult.baseUrl;

  const rows = payload?.events;
  if (!Array.isArray(rows)) {
    throw new Error("Invalid events payload from SofaScore live endpoint");
  }

  const events = [];
  const staleIds = [];
  const changeAges = [];
  const initialSignatures = new Map();

  for (const row of rows.slice(0, maxEvents)) {
    if (!row || typeof row !== "object") {
      continue;
    }

    const eventId = row.id;
    if (typeof eventId !== "number" && typeof eventId !== "string") {
      continue;
    }
    const eventIdStr = String(eventId);

    let oddsResult;
    try {
      oddsResult = await fetchEventOdds(eventIdStr, timeoutMs);
    } catch {
      continue;
    }
    const odds = oddsResult.payload;
    baseUrlUsed = oddsResult.baseUrl;
    if (odds?.error && typeof odds.error === "object") {
      continue;
    }

    const { marketQuotes, signatures } = buildMarketQuotes(odds, includePrematch);
    if (marketQuotes.length === 0) {
      continue;
    }

    const startTs = parseIntSafe(row.startTimestamp, Number.NaN);
    if (!Number.isFinite(startTs) || startTs <= 0) {
      continue;
    }

    const tournament = row.tournament || {};
    const uniqueTournament = tournament?.uniqueTournament || {};
    const league = String(uniqueTournament.name || tournament.name || "SofaScore Football");
    const home = String(row.homeTeam?.name || "home");
    const away = String(row.awayTeam?.name || "away");

    const age = eventChangeAge(row, nowTsSec);
    if (typeof age === "number") {
      changeAges.push(age);
      if (age > staleThresholdSec) {
        staleIds.push(eventIdStr);
      }
    }

    events.push({
      event_id: `sofascore:${eventIdStr}`,
      sport: "football",
      league,
      home,
      away,
      start_time_ms: startTs * 1000,
      markets: marketQuotes,
      sources: ["sofascore"],
      data_quality_score: 0.58,
    });

    initialSignatures.set(eventIdStr, signatures);
  }

  const sampleIds = Array.from(initialSignatures.keys()).slice(0, sampleCheck);
  let sampleChanged = 0;
  let sampleUnchanged = 0;
  let sampleFailed = 0;

  for (const eventId of sampleIds) {
    try {
      const oddsResult = await fetchEventOdds(eventId, timeoutMs);
      const odds = oddsResult.payload;
      baseUrlUsed = oddsResult.baseUrl;
      const { signatures } = buildMarketQuotes(odds, includePrematch);
      const oldSig = initialSignatures.get(eventId) || [];
      if (JSON.stringify(signatures) !== JSON.stringify(oldSig)) {
        sampleChanged += 1;
      } else {
        sampleUnchanged += 1;
      }
    } catch {
      sampleFailed += 1;
    }
  }

  const distinctBookCounts = events.map((event) => new Set(event.markets.map((market) => market.bookmaker)).size);
  const staleCount = staleIds.length;

  let freshnessStatus = "unknown";
  if (events.length === 0) {
    freshnessStatus = "no_data";
  } else if (staleCount > events.length / 2) {
    freshnessStatus = "stale_risk";
  } else if (sampleChanged > 0 || staleCount === 0) {
    freshnessStatus = "fresh";
  }

  const maxAge = changeAges.length > 0 ? Math.max(...changeAges) : null;
  const avgAge = changeAges.length > 0 ? changeAges.reduce((sum, age) => sum + age, 0) / changeAges.length : null;

  const diagnostics = {
    source: "sofascore_scraper",
    fetched_at: nowIso(),
    live_events_seen: rows.length,
    events_with_h2h: events.length,
    events_with_at_least_two_books: distinctBookCounts.filter((count) => count >= 2).length,
    max_distinct_books_per_event: distinctBookCounts.length > 0 ? Math.max(...distinctBookCounts) : 0,
    min_distinct_books_per_event: distinctBookCounts.length > 0 ? Math.min(...distinctBookCounts) : 0,
    stale_threshold_sec: staleThresholdSec,
    stale_events_count: staleCount,
    stale_event_ids: staleIds.slice(0, 10),
    max_change_age_sec: typeof maxAge === "number" ? Number(maxAge.toFixed(2)) : null,
    avg_change_age_sec: typeof avgAge === "number" ? Number(avgAge.toFixed(2)) : null,
    sample_checked: sampleIds.length,
    sample_changed: sampleChanged,
    sample_unchanged: sampleUnchanged,
    sample_failed: sampleFailed,
    freshness_status: freshnessStatus,
    base_url_used: baseUrlUsed,
    feed_note:
      "Scraper defaults to live-only quotes to avoid mixing live/prematch prices. SofaScore web odds usually expose a single upstream feed (sourceId=1), so true multi-book arbitrage coverage is limited.",
  };

  if (events.length > 0 && diagnostics.events_with_at_least_two_books === 0) {
    diagnostics.arbitrage_blocker =
      "No event has >=2 distinct bookmakers in scraped feed; arbitrage scan returns 0.";
  }

  return { events, diagnostics };
}
