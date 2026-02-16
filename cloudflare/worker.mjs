import { handleHealthRequest, handleOptions, handleScanRequest } from "./shared/api.mjs";

export default {
  async fetch(request, env) {
    const url = new URL(request.url);

    if (request.method === "OPTIONS") {
      return handleOptions(env);
    }

    if (url.pathname === "/api/scan" && request.method === "GET") {
      return handleScanRequest(request, env);
    }

    if (url.pathname === "/api/health" && request.method === "GET") {
      return handleHealthRequest(request, env);
    }

    if (env && env.ASSETS) {
      return env.ASSETS.fetch(request);
    }

    return new Response("Not Found", { status: 404 });
  },
};
