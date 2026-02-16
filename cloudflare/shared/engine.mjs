import { clamp, clamp01 } from "./helpers.mjs";

export function computeEventQuality(event) {
  if (typeof event.data_quality_score === "number" && event.data_quality_score > 0) {
    return clamp01(event.data_quality_score);
  }

  const distinctBooks = new Set((event.markets || []).map((market) => market.bookmaker)).size;
  const marketCount = (event.markets || []).length;
  const sourceCount = Array.isArray(event.sources) && event.sources.length > 0 ? event.sources.length : 1;

  const bookScore = Math.min(1, distinctBooks / 6);
  const marketScore = Math.min(1, marketCount / 12);
  const sourceScore = Math.min(1, sourceCount / 2);

  return clamp01(0.55 * bookScore + 0.3 * sourceScore + 0.15 * marketScore);
}

function capForOdd(limit, odd) {
  const caps = [];
  if (typeof limit.max_stake === "number" && limit.max_stake > 0) {
    caps.push(limit.max_stake);
  }
  if (typeof limit.max_payout === "number" && limit.max_payout > 0 && odd > 0) {
    caps.push(limit.max_payout / odd);
  }
  if (caps.length === 0) {
    return null;
  }
  return Math.min(...caps);
}

function effectiveOdd(bookmaker, odd, bookmakerLimits) {
  const limit = bookmakerLimits?.[bookmaker];
  if (!limit) {
    return odd;
  }
  const commission = clamp(Number(limit.commission_pct || 0), 0, 100);
  return 1 + (odd - 1) * (1 - commission / 100);
}

function groupByMarket(markets) {
  const grouped = new Map();
  for (const market of markets || []) {
    const key = market.market_key;
    if (!grouped.has(key)) {
      grouped.set(key, []);
    }
    grouped.get(key).push(market);
  }
  return grouped;
}

function bestOutcomes(marketQuotes, bookmakerLimits) {
  const bestOdds = {};
  const bestBooks = {};
  const outcomesSeen = new Set();

  for (const quote of marketQuotes || []) {
    for (const outcome of quote.outcomes || []) {
      outcomesSeen.add(outcome.key);
      const eff = effectiveOdd(quote.bookmaker, Number(outcome.odds), bookmakerLimits);
      if (!(outcome.key in bestOdds) || eff > bestOdds[outcome.key]) {
        bestOdds[outcome.key] = eff;
        bestBooks[outcome.key] = quote.bookmaker;
      }
    }
  }

  if (outcomesSeen.size < 2) {
    return null;
  }
  if (Object.keys(bestOdds).length !== outcomesSeen.size) {
    return null;
  }
  return { bestOdds, bestBooks };
}

function buildStakePlan(odds, books, bankroll, bookmakerLimits) {
  const implied = {};
  let totalImplied = 0;

  for (const [outcomeKey, odd] of Object.entries(odds)) {
    const probability = 1 / odd;
    implied[outcomeKey] = probability;
    totalImplied += probability;
  }

  const allocations = {};
  for (const [outcomeKey, probability] of Object.entries(implied)) {
    allocations[outcomeKey] = bankroll * (probability / totalImplied);
  }

  let usedBankroll = bankroll;
  let constraintsApplied = false;
  const notes = [];

  const scales = [];
  for (const [outcomeKey, stake] of Object.entries(allocations)) {
    const bookmaker = books[outcomeKey] || "";
    const limit = bookmakerLimits?.[bookmaker];
    if (!limit) {
      continue;
    }
    const cap = capForOdd(limit, odds[outcomeKey]);
    if (typeof cap === "number" && stake > cap) {
      constraintsApplied = true;
      notes.push(`${bookmaker}:${outcomeKey} cap=${cap.toFixed(2)}`);
      if (stake > 0) {
        scales.push(cap / stake);
      }
    }
  }

  if (scales.length > 0) {
    const scale = clamp(Math.min(...scales), 0, 1);
    for (const key of Object.keys(allocations)) {
      allocations[key] *= scale;
    }
    usedBankroll = Object.values(allocations).reduce((sum, value) => sum + value, 0);
  }

  for (const [outcomeKey, stake] of Object.entries(allocations)) {
    const bookmaker = books[outcomeKey] || "";
    const limit = bookmakerLimits?.[bookmaker];
    if (!limit) {
      continue;
    }
    const minStake = Math.max(0, Number(limit.min_stake || 0));
    if (stake < minStake) {
      return null;
    }
  }

  let guaranteedReturn = Number.POSITIVE_INFINITY;
  for (const key of Object.keys(allocations)) {
    guaranteedReturn = Math.min(guaranteedReturn, allocations[key] * odds[key]);
  }
  const guaranteedProfit = guaranteedReturn - usedBankroll;
  const guaranteedProfitPct = usedBankroll > 0 ? (guaranteedProfit / usedBankroll) * 100 : 0;

  return {
    requested_bankroll: bankroll,
    bankroll: usedBankroll,
    total_implied_probability: totalImplied,
    allocations,
    guaranteed_return: guaranteedReturn,
    guaranteed_profit: guaranteedProfit,
    guaranteed_profit_pct: guaranteedProfitPct,
    constraints_applied: constraintsApplied,
    constraints_note: notes.join("; "),
  };
}

export function scanEvents(events, options) {
  const opportunities = [];

  const minMarginPct = Number(options.min_margin_pct || 0);
  const minDistinctBooks = Math.max(2, Number(options.min_distinct_books || 2));
  const minEventSources = Math.max(1, Number(options.min_event_sources || 1));
  const minDataQuality = clamp01(Number(options.min_data_quality || 0));
  const slippageBps = Math.max(0, Number(options.slippage_bps || 0));
  const rejectionRate = clamp(Number(options.rejection_rate || 0), 0, 1);
  const bankroll = Number(options.bankroll || 1000);
  const bookmakerLimits = options.bookmaker_limits || {};

  for (const event of events || []) {
    const sourceCount = Array.isArray(event.sources) && event.sources.length > 0 ? event.sources.length : 1;
    if (sourceCount < minEventSources) {
      continue;
    }

    const quality = computeEventQuality(event);
    if (quality < minDataQuality) {
      continue;
    }

    const grouped = groupByMarket(event.markets || []);
    for (const [marketKey, marketQuotes] of grouped.entries()) {
      const best = bestOutcomes(marketQuotes, bookmakerLimits);
      if (!best) {
        continue;
      }

      const { bestOdds, bestBooks } = best;
      const totalImplied = Object.values(bestOdds).reduce((sum, odd) => sum + 1 / odd, 0);
      if (totalImplied >= 1) {
        continue;
      }

      const marginPct = (1 / totalImplied - 1) * 100;
      if (marginPct < minMarginPct) {
        continue;
      }
      if (new Set(Object.values(bestBooks)).size < minDistinctBooks) {
        continue;
      }

      const stakePlan = buildStakePlan(bestOdds, bestBooks, bankroll, bookmakerLimits);
      if (!stakePlan) {
        continue;
      }

      const slippagePenalty = (stakePlan.bankroll * slippageBps) / 10000;
      const rejectionPenalty = stakePlan.guaranteed_profit * rejectionRate;
      const riskPenalty = slippagePenalty + rejectionPenalty;
      const expectedProfit = stakePlan.guaranteed_profit - riskPenalty;
      const riskScore = clamp(rejectionRate * 65 + slippageBps / 4 + (1 - quality) * 35, 0, 100);

      opportunities.push({
        event_id: event.event_id,
        event: `${event.home} vs ${event.away}`,
        market: marketKey,
        outcomes: bestOdds,
        bookmakers: bestBooks,
        implied: totalImplied,
        margin_pct: marginPct,
        requested_bankroll: stakePlan.requested_bankroll,
        bankroll: stakePlan.bankroll,
        stakes: stakePlan.allocations,
        guaranteed_return: stakePlan.guaranteed_return,
        profit: stakePlan.guaranteed_profit,
        profit_pct: stakePlan.guaranteed_profit_pct,
        expected_profit: expectedProfit,
        risk_penalty: riskPenalty,
        risk_score: riskScore,
        source_count: sourceCount,
        data_quality_score: quality,
        constraints_applied: stakePlan.constraints_applied,
        constraints_note: stakePlan.constraints_note,
      });
    }
  }

  opportunities.sort(
    (a, b) =>
      b.expected_profit - a.expected_profit ||
      b.margin_pct - a.margin_pct ||
      b.data_quality_score - a.data_quality_score,
  );

  return opportunities;
}
