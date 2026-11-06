import type { Link, LinkCreateBody, LinkPatchBody } from "@/api/types";
import { isoToLocalInput, localInputToIso } from "./format";

export interface LinkFormValues {
  destination_url: string;
  alias: string;
  title: string;
  /** Text of a datetime-local input, in the user's time zone. "" = no expiry. */
  expires_at: string;
}

export function emptyLinkValues(): LinkFormValues {
  return { destination_url: "", alias: "", title: "", expires_at: "" };
}

export function valuesFromLink(link: Link): LinkFormValues {
  return {
    destination_url: link.destination_url,
    alias: link.code,
    title: link.title ?? "",
    expires_at: link.expires_at ? isoToLocalInput(link.expires_at) : "",
  };
}

/** Empty optional fields are left out, not sent as empty strings (the API would reject "" titles). */
export function buildCreateBody(values: LinkFormValues): LinkCreateBody {
  const body: LinkCreateBody = { destination_url: values.destination_url.trim() };
  const alias = values.alias.trim();
  const title = values.title.trim();
  const expires = localInputToIso(values.expires_at);
  if (alias) body.alias = alias;
  if (title) body.title = title;
  if (expires) body.expires_at = expires;
  return body;
}

/**
 * Only what changed. PATCH semantics: an omitted field stays as it is; null clears a title or an
 * expiry. An empty object means "nothing to save".
 */
export function buildPatchBody(link: Link, values: LinkFormValues): LinkPatchBody {
  const body: LinkPatchBody = {};
  const destination = values.destination_url.trim();
  if (destination !== link.destination_url) body.destination_url = destination;

  const title = values.title.trim();
  if (title !== (link.title ?? "")) body.title = title === "" ? null : title;

  // Compare the input texts: the input has minute precision, the stored value may have seconds,
  // and an untouched field must not count as a change.
  const before = link.expires_at ? isoToLocalInput(link.expires_at) : "";
  if (values.expires_at !== before) body.expires_at = localInputToIso(values.expires_at);
  return body;
}
