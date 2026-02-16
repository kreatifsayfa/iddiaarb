import { fetchJson, fetchText, nowIso, parseFloatSafe, parseIntSafe } from "./helpers.mjs";

const LIST_URL = "https://www.betexplorer.com/football/";

function stripTags(rawHtml) {
  const stripped = String(rawHtml || "").replace(/<[^>]+>/g, "").trim();
  const htmlEntities = {
    nbsp: " ",
    amp: "&",
    lt: "<",
    gt: ">",
    quot: '"',
    apos: "'",
    "#39": "'",
  };
  return stripped.replace(/&([a-zA-Z0-9#]+);/g, (match, entity) => {
    return Object.prototype.hasOwnProperty.call(htmlEntities, entity) ? htmlEntities[entity] : match;
  });
}

function parseDt(raw) {
  const parts = String(raw || "")
    .split(",")
    .map((part) => Number.parseInt(part.trim(), 10));
  if (parts.length !== 5 || parts.some((part) => !Number.isFinite(part))) {
    return null;
  }
  const [day, month, year, hour, minute] = parts;
  const ts = Date.UTC(year, month - 1, day, hour, minute, 0, 0);
  if (!Number.isFinite(ts)) {
    return null;
  }
  return ts;
}

function parseEventsTable(pageHtml, maxRows) {
  const rowPattern = /(<tr[^>]*>)(.*?)<\/tr>/gis;
  const out = [];
  let currentLeague = "BetExplorer Football";
  let rowMatch;

  while ((rowMatch = rowPattern.exec(pageHtml)) !== null) {
    const rowOpen = rowMatch[1] || "";
    const rowBody = rowMatch[2] || "";

    if (rowOpen.includes("js-tournament")) {
      const leagueMatch = /<a[^>]*class="table-main__tournament"[^>]*>(.*?)<\/a>/is.exec(rowBody);
      if (leagueMatch && leagueMatch[1]) {
        currentLeague = stripTags(leagueMatch[1]) || currentLeague;
      }
      continue;
    }

    const dtMatch = /data-dt="([^"]+)"/i.exec(rowOpen);
    if (!dtMatch || !dtMatch[1]) {
      continue;
    }
    const startTimeMs = parseDt(dtMatch[1]);
    if (!Number.isFinite(startTimeMs) || startTimeMs <= 0) {
      continue;
    }

    const hrefMatch = /<a href="(\/football\/[^"]+\/([A-Za-z0-9]+)\/)"[^>]*>(.*?)<\/a>/is.exec(rowBody);
    if (!hrefMatch) {
      continue;
    }
    const matchPath = hrefMatch[1];
    const matchId = hrefMatch[2];
    const teamsText = stripTags(hrefMatch[3]);
    if (!teamsText.includes(" - ")) {
      continue;
    }
    const [homeRaw, awayRaw] = teamsText.split(" - ", 2);
    const home = String(homeRaw || "").trim();
    const away = String(awayRaw || "").trim();
    if (!home || !away) {
      continue;
    }

    out.push({
      match_id: matchId,
      match_path: matchPath,
      league: currentLeague,
      home,
      away,
      start_time_ms: startTimeMs,
    });
    if (out.length >= maxRows) {
      break;
    }
  }

  return out;
}

async function fetchMatchOddsHtml(matchId, matchPath, lang, timeoutMs) {
  const url = `https://www.betexplorer.com/match-odds/${matchId}/0/1x2/odds/?lang=${lang}`;
  const payload = await fetchJson(url, {
    timeoutMs,
    headers: {
      Accept: "application/json, text/plain, */*",
      "User-Agent": "Mozilla/5.0",
      Referer: `https://www.betexplorer.com${matchPath}`,
      Origin: "https://www.betexplorer.com",
    },
  });
  const oddsHtml = payload?.odds;
  if (typeof oddsHtml !== "string" || !oddsHtml.trim()) {
    throw new Error(`missing_odds_html:${matchId}`);
  }
  return oddsHtml;
}

function parseMarketQuotes(oddsHtml) {
  const rows = [];
  rows.push(...(oddsHtml.match(/<tr[^>]*data-bid="[0-9]+"[^>]*>[\s\S]*?<\/tr>/gi) || []));
  rows.push(
    ...(
      oddsHtml.match(
        /<div[^>]*oddsComparisonAll__rowBookie[^>]*data-bid="[0-9]+"[^>]*>[\s\S]*?(?=<div[^>]*oddsComparisonAll__rowBookie|<div id="match-add-to-selection"|$)/gi,
      ) || []
    ),
  );
  const markets = [];

  for (const rowHtml of rows) {
    const bookMatch = /in-bookmaker-logo-link[^>]*>([^<]+)<\/a>/is.exec(rowHtml);
    if (!bookMatch || !bookMatch[1]) {
      continue;
    }
    const bookmaker = stripTags(bookMatch[1]);
    if (!bookmaker) {
      continue;
    }

    const outcomes = {};
    const pairPattern =
      /data-odd="([0-9]+(?:\.[0-9]+)?)"[^>]*data-pos="([012])"|data-pos="([012])"[^>]*data-odd="([0-9]+(?:\.[0-9]+)?)/gi;
    let pair;
    while ((pair = pairPattern.exec(rowHtml)) !== null) {
      const odd = parseFloatSafe(pair[1] || pair[4], Number.NaN);
      const pos = pair[2] || pair[3] || "";
      if (!["0", "1", "2"].includes(pos)) {
        continue;
      }
      if (!Number.isFinite(odd) || odd <= 1) {
        continue;
      }
      outcomes[pos] = Math.max(Number(outcomes[pos] || 0), odd);
    }

    if (!Object.prototype.hasOwnProperty.call(outcomes, "0")) {
      continue;
    }
    if (!Object.prototype.hasOwnProperty.call(outcomes, "1")) {
      continue;
    }
    if (!Object.prototype.hasOwnProperty.call(outcomes, "2")) {
      continue;
    }

    markets.push({
      bookmaker,
      market_key: "h2h_prematch",
      outcomes: [
        { key: "HOME", label: "1", odds: outcomes["1"] },
        { key: "DRAW", label: "X", odds: outcomes["0"] },
        { key: "AWAY", label: "2", odds: outcomes["2"] },
      ],
      source: "betexplorer",
      quality_score: 0.78,
    });
  }

  return markets;
}

export async function loadBetExplorerEvents(options) {
  const now = nowIso();
  const maxEvents = Math.max(1, parseIntSafe(options.max_events, 25));
  const timeoutMs = Math.max(5000, parseIntSafe(options.timeout_ms, 20000));
  const lang = String(options.betexplorer_lang || "en").trim().toLowerCase() || "en";
  const scanLimit = Math.min(Math.max(maxEvents * 6, 20), 300);

  const listHtml = await fetchText(LIST_URL, {
    timeoutMs,
    headers: {
      Accept: "text/html,application/xhtml+xml",
      "User-Agent": "Mozilla/5.0",
      Referer: "https://www.google.com/",
    },
  });

  const listedEvents = parseEventsTable(listHtml, scanLimit);
  if (listedEvents.length === 0) {
    return {
      events: [],
      diagnostics: {
        source: "betexplorer_scraper",
        fetched_at: now,
        events_listed: 0,
        events_scan_limit: scanLimit,
        events_with_h2h: 0,
        odds_failed: 0,
        events_with_at_least_two_books: 0,
        max_distinct_books_per_event: 0,
        min_distinct_books_per_event: 0,
        arbitrage_blocker: "BetExplorer listing parsed 0 events.",
      },
    };
  }

  const events = [];
  let oddsFailed = 0;
  const booksPerEvent = [];

  for (const row of listedEvents) {
    try {
      const oddsHtml = await fetchMatchOddsHtml(row.match_id, row.match_path, lang, timeoutMs);
      const markets = parseMarketQuotes(oddsHtml);
      if (markets.length === 0) {
        continue;
      }

      events.push({
        event_id: `betexplorer:${row.match_id}`,
        sport: "football",
        league: row.league,
        home: row.home,
        away: row.away,
        start_time_ms: row.start_time_ms,
        markets,
        sources: ["betexplorer"],
        data_quality_score: 0.79,
      });
      booksPerEvent.push(new Set(markets.map((market) => market.bookmaker)).size);
      if (events.length >= maxEvents) {
        break;
      }
    } catch {
      oddsFailed += 1;
    }
  }

  const eventsWithTwoBooks = booksPerEvent.filter((count) => count >= 2).length;
  const diagnostics = {
    source: "betexplorer_scraper",
    fetched_at: now,
    events_listed: listedEvents.length,
    events_scan_limit: scanLimit,
    events_with_h2h: events.length,
    odds_failed: oddsFailed,
    events_with_at_least_two_books: eventsWithTwoBooks,
    max_distinct_books_per_event: booksPerEvent.length > 0 ? Math.max(...booksPerEvent) : 0,
    min_distinct_books_per_event: booksPerEvent.length > 0 ? Math.min(...booksPerEvent) : 0,
    lang,
    list_url: LIST_URL,
  };

  if (events.length > 0 && eventsWithTwoBooks === 0) {
    diagnostics.arbitrage_blocker =
      "No event has >=2 distinct bookmakers in scraped BetExplorer odds tables.";
  }

  return { events, diagnostics };
}
