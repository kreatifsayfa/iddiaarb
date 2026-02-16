import { fetchJson, fetchText, nowIso, parseFloatSafe, parseIntSafe } from "./helpers.mjs";

const FEED_URL = "https://2.flashscore.ninja/2/x/feed/f_1_0_3_en_1";
const ODDS_URL = "https://global.ds.lsapp.eu/odds/pq_graphql";
const PROJECT_ID = "2";

function splitFeedToken(token) {
  let idx = token.indexOf("÷");
  if (idx < 0) {
    idx = token.indexOf("Ã·");
  }
  if (idx < 0) {
    return null;
  }
  return {
    key: token.slice(0, idx),
    value: token.slice(idx + (token[idx] === "÷" ? 1 : 2)),
  };
}

function parseFeed(raw) {
  const sep = String.fromCharCode(172);
  const tokens = raw.split(sep);

  const events = [];
  let currentLeague = "Flashscore Football";
  let currentEvent = null;

  const flushEvent = () => {
    if (!currentEvent) {
      return;
    }
    const eventId = String(currentEvent.event_id || "").trim();
    const home = String(currentEvent.home || "").trim();
    const away = String(currentEvent.away || "").trim();
    const startTs = currentEvent.start_ts;

    if (eventId.length >= 6 && home && away && Number.isInteger(startTs) && startTs > 0) {
      events.push(currentEvent);
    }
    currentEvent = null;
  };

  for (const token of tokens) {
    const split = splitFeedToken(token);
    if (!split) {
      continue;
    }

    const { key, value } = split;
    if (key === "~ZA") {
      flushEvent();
      const leagueName = String(value || "").trim();
      if (leagueName) {
        currentLeague = leagueName;
      }
      continue;
    }

    if (key === "~AA") {
      flushEvent();
      currentEvent = {
        event_id: String(value || "").trim(),
        league: currentLeague,
        start_ts: null,
        home: "",
        away: "",
      };
      continue;
    }

    if (!currentEvent) {
      continue;
    }

    if (key === "AD") {
      const parsed = parseIntSafe(String(value || "").trim(), Number.NaN);
      currentEvent.start_ts = Number.isFinite(parsed) ? parsed : null;
    } else if (key === "AE") {
      currentEvent.home = String(value || "").trim();
    } else if (key === "AF") {
      currentEvent.away = String(value || "").trim();
    }
  }

  flushEvent();
  return events;
}

function extractBooks(menu, maxBookmakersPerEvent) {
  const root = menu?.data?.getPrematchOddsBettingTypeMenu;
  const settings = root?.settings;
  const items = root?.items;
  if (!Array.isArray(items)) {
    return [];
  }

  let targetItem = null;
  for (const item of items) {
    if (!item || typeof item !== "object") {
      continue;
    }
    if (!item.isActive) {
      continue;
    }
    if (item.bettingType !== "HOME_DRAW_AWAY") {
      continue;
    }
    if (item.bettingScope !== "FULL_TIME") {
      continue;
    }
    targetItem = item;
    break;
  }
  if (!targetItem) {
    return [];
  }

  const bookNameMap = new Map();
  const settingsBooks = settings?.bookmakers;
  if (Array.isArray(settingsBooks)) {
    for (const row of settingsBooks) {
      const bookmaker = row?.bookmaker;
      if (!bookmaker || typeof bookmaker !== "object") {
        continue;
      }
      const bookId = bookmaker.id;
      if (!Number.isInteger(bookId)) {
        continue;
      }
      const name = String(bookmaker.name || `bookmaker_${bookId}`).trim();
      bookNameMap.set(bookId, name);
    }
  }

  const out = [];
  const seen = new Set();
  const ids = Array.isArray(targetItem.bookmakerIds) ? targetItem.bookmakerIds : [];
  for (const rawId of ids) {
    if (!Number.isInteger(rawId)) {
      continue;
    }
    if (seen.has(rawId)) {
      continue;
    }
    const name = bookNameMap.get(rawId);
    if (!name) {
      continue;
    }
    seen.add(rawId);
    out.push([rawId, name]);
    if (out.length >= maxBookmakersPerEvent) {
      break;
    }
  }
  return out;
}

async function fetchFeedEvents(timeoutMs) {
  const raw = await fetchText(FEED_URL, {
    timeoutMs,
    headers: {
      Accept: "text/plain,*/*",
      "User-Agent": "Mozilla/5.0",
      Referer: "https://www.flashscore.com/",
      "x-fsign": "SW9D1eZo",
      "x-geoip": "1",
    },
  });
  return parseFeed(raw);
}

function oddsUrlCandidates(params) {
  const urls = [
    `${ODDS_URL}?${params.toString()}`,
    `https://${PROJECT_ID}.ds.lsapp.eu/pq_graphql?${params.toString()}`,
    `https://${PROJECT_ID}.ds.lsapp.eu/odds/pq_graphql?${params.toString()}`,
  ];
  return [...new Set(urls)];
}

async function fetchJsonWithFallback(urls, options) {
  let lastError = null;
  for (const url of urls) {
    try {
      const payload = await fetchJson(url, options);
      return { payload, urlUsed: url };
    } catch (error) {
      lastError = error;
    }
  }
  throw lastError || new Error("all_odds_urls_failed");
}

async function fetchMenu(eventId, geoIpCode, geoIpSubdivisionCode, timeoutMs) {
  const params = new URLSearchParams({
    _hash: "pobtm",
    eventId,
    projectId: PROJECT_ID,
    geoIpCode,
    geoIpSubdivisionCode,
  });
  const { payload, urlUsed } = await fetchJsonWithFallback(oddsUrlCandidates(params), {
    timeoutMs,
    headers: {
      Accept: "application/json",
      "User-Agent": "Mozilla/5.0",
      Origin: "https://www.flashscore.com",
      Referer: "https://www.flashscore.com/",
    },
  });
  return { payload, urlUsed };
}

function extractH2H(payload) {
  const row = payload?.data?.findPrematchOddsForBookmaker || payload?.data?.findPrematchOdds;
  if (!row || typeof row !== "object") {
    return null;
  }

  const home = parseFloatSafe(row?.home?.value, Number.NaN);
  const draw = parseFloatSafe(row?.draw?.value, Number.NaN);
  const away = parseFloatSafe(row?.away?.value, Number.NaN);

  if (!Number.isFinite(home) || !Number.isFinite(draw) || !Number.isFinite(away)) {
    return null;
  }
  if (home <= 1 || draw <= 1 || away <= 1) {
    return null;
  }

  return { home, draw, away };
}

async function fetchH2H(eventId, bookmakerId, geoIpCode, geoIpSubdivisionCode, timeoutMs) {
  const headers = {
    Accept: "application/json",
    "User-Agent": "Mozilla/5.0",
    Origin: "https://www.flashscore.com",
    Referer: "https://www.flashscore.com/",
  };

  const primary = new URLSearchParams({
    _hash: "ope2",
    eventId,
    bookmakerId: String(bookmakerId),
    betType: "HOME_DRAW_AWAY",
    betScope: "FULL_TIME",
  });

  try {
    const primaryRes = await fetchJsonWithFallback(oddsUrlCandidates(primary), {
      timeoutMs,
      headers,
    });
    const parsed = extractH2H(primaryRes.payload);
    if (parsed) {
      return { prices: parsed, urlUsed: primaryRes.urlUsed };
    }
  } catch {
    // legacy hash fallback below
  }

  const legacy = new URLSearchParams({
    _hash: "ope",
    eventId,
    projectId: PROJECT_ID,
    geoIpCode,
    geoIpSubdivisionCode,
  });
  const legacyRes = await fetchJsonWithFallback(oddsUrlCandidates(legacy), {
    timeoutMs,
    headers,
  });
  return { prices: extractH2H(legacyRes.payload), urlUsed: legacyRes.urlUsed };
}

export async function loadFlashscoreEvents(options) {
  const now = nowIso();
  const maxEvents = Math.max(1, parseIntSafe(options.max_events, 40));
  const timeoutMs = Math.max(5000, parseIntSafe(options.timeout_ms, 20000));
  const geoIpCode = String(options.geo_ip_code || "GB").trim().toUpperCase();
  const geoIpSubdivisionCode = String(options.geo_ip_subdivision || "GBENG").trim().toUpperCase();
  const maxBookmakersPerEvent = Math.max(1, parseIntSafe(options.max_bookmakers_per_event, 8));
  let oddsUrlUsed = ODDS_URL;

  const feedRows = await fetchFeedEvents(timeoutMs);

  if (feedRows.length === 0) {
    return {
      events: [],
      diagnostics: {
        source: "flashscore_scraper",
        fetched_at: now,
        feed_events_seen: 0,
        events_attempted: 0,
        events_with_h2h: 0,
        events_with_at_least_two_books: 0,
        max_distinct_books_per_event: 0,
        min_distinct_books_per_event: 0,
        menu_failed: 0,
        odds_failed: 0,
        geo_ip_code: geoIpCode,
        geo_ip_subdivision_code: geoIpSubdivisionCode,
        arbitrage_blocker: "Flashscore feed parsed 0 events.",
      },
    };
  }

  const events = [];
  let menuFailed = 0;
  let oddsFailed = 0;
  const booksPerEvent = [];

  const scanLimit = Math.min(feedRows.length, Math.max(20, maxEvents * 4));
  const usedRows = feedRows.slice(0, scanLimit);
  let rowsScanned = 0;

  for (const row of usedRows) {
    rowsScanned += 1;
    const eventId = String(row.event_id || "");

    let menu;
    try {
      const fetched = await fetchMenu(eventId, geoIpCode, geoIpSubdivisionCode, timeoutMs);
      menu = fetched.payload;
      oddsUrlUsed = fetched.urlUsed.split("?", 1)[0];
    } catch {
      menuFailed += 1;
      continue;
    }

    const books = extractBooks(menu, maxBookmakersPerEvent);
    if (books.length === 0) {
      continue;
    }

    const markets = [];
    for (const [bookId, bookName] of books) {
      try {
        const fetched = await fetchH2H(eventId, bookId, geoIpCode, geoIpSubdivisionCode, timeoutMs);
        oddsUrlUsed = fetched.urlUsed.split("?", 1)[0];
        if (!fetched.prices) {
          continue;
        }
        markets.push({
          bookmaker: bookName,
          market_key: "h2h_prematch",
          outcomes: [
            { key: "HOME", label: "1", odds: fetched.prices.home },
            { key: "DRAW", label: "X", odds: fetched.prices.draw },
            { key: "AWAY", label: "2", odds: fetched.prices.away },
          ],
          source: "flashscore",
          quality_score: 0.75,
        });
      } catch {
        oddsFailed += 1;
      }
    }

    if (markets.length === 0) {
      continue;
    }

    const startTs = parseIntSafe(row.start_ts, Number.NaN);
    if (!Number.isFinite(startTs) || startTs <= 0) {
      continue;
    }

    events.push({
      event_id: `flashscore:${eventId}`,
      sport: "football",
      league: String(row.league || "Flashscore Football"),
      home: String(row.home || ""),
      away: String(row.away || ""),
      start_time_ms: startTs * 1000,
      markets,
      sources: ["flashscore"],
      data_quality_score: 0.74,
    });

    booksPerEvent.push(new Set(markets.map((market) => market.bookmaker)).size);

    if (events.length >= maxEvents) {
      break;
    }
  }

  const eventsWithTwoBooks = booksPerEvent.filter((count) => count >= 2).length;
  const diagnostics = {
    source: "flashscore_scraper",
    fetched_at: now,
    feed_events_seen: feedRows.length,
    events_attempted: rowsScanned,
    feed_scan_limit: scanLimit,
    events_with_h2h: events.length,
    events_with_at_least_two_books: eventsWithTwoBooks,
    max_distinct_books_per_event: booksPerEvent.length > 0 ? Math.max(...booksPerEvent) : 0,
    min_distinct_books_per_event: booksPerEvent.length > 0 ? Math.min(...booksPerEvent) : 0,
    menu_failed: menuFailed,
    odds_failed: oddsFailed,
    geo_ip_code: geoIpCode,
    geo_ip_subdivision_code: geoIpSubdivisionCode,
    odds_url_used: oddsUrlUsed,
    feed_note:
      "Bookmaker coverage depends on geoIpCode/geoIpSubdivisionCode. For broader coverage use markets like GB/GBENG, US/USNJ, BR/BRSP.",
  };

  if (events.length > 0 && eventsWithTwoBooks === 0) {
    diagnostics.arbitrage_blocker =
      "No event has >=2 distinct bookmakers for selected geo; adjust geo settings.";
  }

  return { events, diagnostics };
}
