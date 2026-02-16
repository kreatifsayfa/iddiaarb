import { clamp, normalizeTeamName } from "./helpers.mjs";

const STOPWORDS = new Set([
  "fc",
  "fk",
  "sk",
  "club",
  "cf",
  "sc",
  "as",
  "ac",
  "if",
  "ca",
  "cd",
  "cs",
  "de",
  "del",
  "la",
  "las",
  "los",
  "y",
  "atletico",
  "athletico",
  "deportivo",
]);

function levenshteinDistance(a, b) {
  if (a === b) {
    return 0;
  }
  const aLen = a.length;
  const bLen = b.length;
  if (aLen === 0) {
    return bLen;
  }
  if (bLen === 0) {
    return aLen;
  }

  const prev = new Array(bLen + 1);
  const next = new Array(bLen + 1);
  for (let j = 0; j <= bLen; j += 1) {
    prev[j] = j;
  }

  for (let i = 1; i <= aLen; i += 1) {
    next[0] = i;
    for (let j = 1; j <= bLen; j += 1) {
      const cost = a[i - 1] === b[j - 1] ? 0 : 1;
      next[j] = Math.min(prev[j] + 1, next[j - 1] + 1, prev[j - 1] + cost);
    }
    for (let j = 0; j <= bLen; j += 1) {
      prev[j] = next[j];
    }
  }

  return prev[bLen];
}

function ratio(a, b) {
  const left = String(a || "");
  const right = String(b || "");
  if (!left && !right) {
    return 1;
  }
  if (!left || !right) {
    return 0;
  }
  const maxLen = Math.max(left.length, right.length);
  if (maxLen === 0) {
    return 1;
  }
  const distance = levenshteinDistance(left, right);
  return clamp(1 - distance / maxLen, 0, 1);
}

function tokenSetRatio(a, b) {
  const sa = new Set(String(a || "").split(/\s+/).filter(Boolean));
  const sb = new Set(String(b || "").split(/\s+/).filter(Boolean));
  if (sa.size === 0 && sb.size === 0) {
    return 1;
  }
  if (sa.size === 0 || sb.size === 0) {
    return 0;
  }

  let intersection = 0;
  for (const token of sa) {
    if (sb.has(token)) {
      intersection += 1;
    }
  }
  const union = sa.size + sb.size - intersection;
  return union > 0 ? intersection / union : 0;
}

export function eventConfidence(left, right, maxStartDeltaMinutes = 120) {
  const lHome = normalizeTeamName(left.home, STOPWORDS);
  const lAway = normalizeTeamName(left.away, STOPWORDS);
  const rHome = normalizeTeamName(right.home, STOPWORDS);
  const rAway = normalizeTeamName(right.away, STOPWORDS);

  const straight = (ratio(lHome, rHome) + ratio(lAway, rAway)) / 2;
  const swapped = (ratio(lHome, rAway) + ratio(lAway, rHome)) / 2;
  const straightToken = (tokenSetRatio(lHome, rHome) + tokenSetRatio(lAway, rAway)) / 2;
  const swappedToken = (tokenSetRatio(lHome, rAway) + tokenSetRatio(lAway, rHome)) / 2;

  const nameScore = Math.max(
    0.72 * straight + 0.28 * straightToken,
    (0.72 * swapped + 0.28 * swappedToken) * 0.95,
  );

  const deltaMinutes = Math.abs(left.start_time_ms - right.start_time_ms) / 60000;
  const timeScore =
    deltaMinutes > maxStartDeltaMinutes ? 0 : 1 - deltaMinutes / Math.max(1, maxStartDeltaMinutes);

  const leagueScore = ratio(normalizeTeamName(left.league, STOPWORDS), normalizeTeamName(right.league, STOPWORDS));
  const sportScore = left.sport === right.sport ? 1 : 0;

  const score = 0.64 * nameScore + 0.24 * timeScore + 0.07 * leagueScore + 0.05 * sportScore;
  return clamp(score, 0, 1);
}

export function mergeSimilarEvents(events, minConfidence = 0.86, maxStartDeltaMinutes = 120) {
  if (!Array.isArray(events) || events.length === 0) {
    return { merged: [], decisions: [] };
  }

  const ordered = [...events].sort((a, b) => a.start_time_ms - b.start_time_ms);
  const merged = [];
  const decisions = [];

  for (const event of ordered) {
    let bestIndex = -1;
    let bestScore = -1;

    for (let i = 0; i < merged.length; i += 1) {
      const candidate = merged[i];
      if (Math.abs(event.start_time_ms - candidate.start_time_ms) > maxStartDeltaMinutes * 60000) {
        continue;
      }
      const score = eventConfidence(event, candidate, maxStartDeltaMinutes);
      if (score > bestScore) {
        bestScore = score;
        bestIndex = i;
      }
    }

    if (bestIndex >= 0 && bestScore >= minConfidence) {
      const prior = merged[bestIndex];
      merged[bestIndex] = {
        ...prior,
        event_id: `${prior.event_id}|${event.event_id}`,
        markets: [...prior.markets, ...event.markets],
        sources: [...new Set([...(prior.sources || []), ...(event.sources || [])])].sort(),
        data_quality_score: Math.max(Number(prior.data_quality_score || 0), Number(event.data_quality_score || 0)),
      };
      decisions.push({
        source_event_id: event.event_id,
        target_event_id: prior.event_id,
        confidence: bestScore,
      });
    } else {
      merged.push(event);
    }
  }

  return { merged, decisions };
}
