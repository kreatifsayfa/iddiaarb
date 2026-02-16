import { handleOptions, handleScanRequest } from "../../cloudflare/shared/api.mjs";

export const onRequestOptions = async (context) => {
  return handleOptions(context.env);
};

export const onRequestGet = async (context) => {
  return handleScanRequest(context.request, context.env);
};
