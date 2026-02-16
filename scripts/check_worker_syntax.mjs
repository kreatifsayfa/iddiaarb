import { handleHealthRequest, handleScanRequest } from "../cloudflare/shared/api.mjs";

const env = {
  CACHE_TTL_SECONDS: "20",
  SCRAPER_TIMEOUT_MS: "10000",
  CORS_ORIGIN: "*",
};

const healthReq = new Request("https://example.com/api/health");
const healthRes = await handleHealthRequest(healthReq, env);
if (!(healthRes instanceof Response)) {
  throw new Error("health handler did not return Response");
}

const badReq = new Request("https://example.com/api/scan?bankroll=-1");
const badRes = await handleScanRequest(badReq, env);
if (!(badRes instanceof Response)) {
  throw new Error("scan handler did not return Response");
}
if (badRes.status !== 400) {
  throw new Error(`expected 400 for invalid params, got ${badRes.status}`);
}

console.log("Worker syntax/smoke check passed");
