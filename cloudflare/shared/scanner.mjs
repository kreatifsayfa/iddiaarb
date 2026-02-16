import { scanEvents, computeEventQuality } from "./engine.mjs";
import { nowIso, parseIntSafe } from "./helpers.mjs";
import { mergeSimilarEvents } from "./matcher.mjs";
import { loadBetExplorerEvents } from "./providers_betexplorer.mjs";
import { loadFlashscoreEvents } from "./providers_flashscore.mjs";
import { loadLivesportEvents, loadSoccer24Events } from "./providers_livesport.mjs";
import { loadSofaScoreEvents } from "./providers_sofascore.mjs";

function prepareEvents(rows, source) {
  return (rows || []).map((event) => ({
    ...event,
    sources: [source],
    data_quality_score: Math.max(Number(event.data_quality_score || 0), 0.55),
    markets: (event.markets || []).map((market) => ({
      ...market,
      source,
      quality_score: Math.max(Number(market.quality_score || 0), 0.5),
    })),
  }));
}

async function loadMultiEvents(params) {
  const maxEvents = Math.max(1, parseIntSafe(params.max_events, 40));
  const quorumMinSources = Math.max(1, parseIntSafe(params.quorum_min_sources, 2));
  const matcherConfidence = Number.isFinite(Number(params.matcher_confidence))
    ? Number(params.matcher_confidence)
    : 0.8;

  const includeFlashscore = params.include_flashscore !== false;
  const includeSoccer24 = params.include_soccer24 !== false;
  const includeLivesport = params.include_livesport !== false;
  const includeBetExplorer = params.include_betexplorer !== false;
  const includeSofascore = params.include_sofascore === true;

  const allEvents = [];
  const warnings = [];
  let flashscoreEvents = 0;
  let soccer24Events = 0;
  let livesportEvents = 0;
  let betexplorerEvents = 0;
  let sofascoreEvents = 0;

  const tasks = [];

  if (includeFlashscore) {
    tasks.push(
      loadFlashscoreEvents({ ...params, max_events: maxEvents })
        .then((result) => {
          flashscoreEvents = result.events.length;
          allEvents.push(...prepareEvents(result.events, "flashscore"));
        })
        .catch((error) => {
          warnings.push(`flashscore_error=${String(error?.message || error)}`);
        }),
    );
  }

  if (includeSoccer24) {
    tasks.push(
      loadSoccer24Events({ ...params, max_events: maxEvents })
        .then((result) => {
          soccer24Events = result.events.length;
          allEvents.push(...prepareEvents(result.events, "soccer24"));
        })
        .catch((error) => {
          warnings.push(`soccer24_error=${String(error?.message || error)}`);
        }),
    );
  }

  if (includeLivesport) {
    tasks.push(
      loadLivesportEvents({ ...params, max_events: maxEvents })
        .then((result) => {
          livesportEvents = result.events.length;
          allEvents.push(...prepareEvents(result.events, "livesport"));
        })
        .catch((error) => {
          warnings.push(`livesport_error=${String(error?.message || error)}`);
        }),
    );
  }

  if (includeBetExplorer) {
    tasks.push(
      loadBetExplorerEvents({ ...params, max_events: maxEvents })
        .then((result) => {
          betexplorerEvents = result.events.length;
          allEvents.push(...prepareEvents(result.events, "betexplorer"));
        })
        .catch((error) => {
          warnings.push(`betexplorer_error=${String(error?.message || error)}`);
        }),
    );
  }

  if (includeSofascore) {
    tasks.push(
      loadSofaScoreEvents({ ...params, max_events: maxEvents, include_prematch: true })
        .then((result) => {
          sofascoreEvents = result.events.length;
          allEvents.push(...prepareEvents(result.events, "sofascore"));
        })
        .catch((error) => {
          warnings.push(`sofascore_error=${String(error?.message || error)}`);
        }),
    );
  }

  await Promise.all(tasks);

  if (allEvents.length === 0) {
    throw new Error("No scraper data available from selected sources");
  }

  const { merged, decisions } = mergeSimilarEvents(allEvents, matcherConfidence, 90);

  const quorumEvents = [];
  for (const event of merged) {
    const sourceNames = new Set();
    for (const part of String(event.event_id || "").split("|")) {
      const separator = part.indexOf(":");
      if (separator > 0) {
        sourceNames.add(part.slice(0, separator));
      }
    }
    if (sourceNames.size === 0 && Array.isArray(event.sources)) {
      for (const source of event.sources) {
        sourceNames.add(source);
      }
    }

    const quality = computeEventQuality(event);
    const candidate = {
      ...event,
      sources: sourceNames.size > 0 ? [...sourceNames].sort() : event.sources || [],
      data_quality_score: Math.max(Number(event.data_quality_score || 0), quality),
    };

    if ((candidate.sources || []).length >= quorumMinSources) {
      quorumEvents.push(candidate);
    }
  }

  return {
    events: quorumEvents,
    diagnostics: {
      source: "multi_scraper",
      events_raw_total: allEvents.length,
      flashscore_events: flashscoreEvents,
      soccer24_events: soccer24Events,
      livesport_events: livesportEvents,
      betexplorer_events: betexplorerEvents,
      sofascore_events: sofascoreEvents,
      merged_events: merged.length,
      events_with_quorum: quorumEvents.length,
      quorum_min_sources: quorumMinSources,
      match_links: decisions.length,
      warnings,
    },
  };
}

export async function runScanner(params) {
  const scraperSource = String(params.scraper_source || "flashscore");

  let loaded;
  if (scraperSource === "flashscore") {
    loaded = await loadFlashscoreEvents(params);
  } else if (scraperSource === "soccer24") {
    loaded = await loadSoccer24Events(params);
  } else if (scraperSource === "livesport") {
    loaded = await loadLivesportEvents(params);
  } else if (scraperSource === "betexplorer") {
    loaded = await loadBetExplorerEvents(params);
  } else if (scraperSource === "sofascore") {
    loaded = await loadSofaScoreEvents(params);
  } else if (scraperSource === "multi") {
    loaded = await loadMultiEvents(params);
  } else {
    throw new Error("Invalid scraper_source");
  }

  const opportunities = scanEvents(loaded.events, {
    min_margin_pct: params.min_margin,
    min_distinct_books: 2,
    min_event_sources: params.min_source_quorum,
    min_data_quality: params.min_data_quality,
    bookmaker_limits: params.bookmaker_limits || {},
    slippage_bps: params.slippage_bps,
    rejection_rate: params.rejection_rate,
    bankroll: params.bankroll,
  });

  return {
    count: opportunities.length,
    opportunities,
    meta: {
      generated_at: nowIso(),
      events_in: loaded.events.length,
      ...loaded.diagnostics,
    },
  };
}

export async function runHealthCheck({ checkScraper = false } = {}) {
  const payload = {
    status: "ok",
    timestamp: nowIso(),
    runtime: "cloudflare-worker",
  };

  if (checkScraper) {
    try {
      const loaded = await loadFlashscoreEvents({
        max_events: 2,
        max_bookmakers_per_event: 4,
        geo_ip_code: "GB",
        geo_ip_subdivision: "GBENG",
      });
      payload.scraper_status = "ok";
      payload.scraper_events = loaded.events.length;
    } catch (error) {
      payload.status = "degraded";
      payload.scraper_status = `error: ${String(error?.message || error)}`;
    }
  }

  return payload;
}
