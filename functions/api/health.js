import { handleHealthRequest, handleOptions } from "../../cloudflare/shared/api.mjs";

export const onRequestOptions = async (context) => {
  return handleOptions(context.env);
};

export const onRequestGet = async (context) => {
  return handleHealthRequest(context.request, context.env);
};
