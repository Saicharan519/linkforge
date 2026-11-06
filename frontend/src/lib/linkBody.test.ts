import { describe, expect, it } from "vitest";
import { makeLink } from "@/test/fakeApi";
import { isoToLocalInput, localInputToIso } from "./format";
import { buildCreateBody, buildPatchBody, emptyLinkValues, valuesFromLink } from "./linkBody";

describe("buildCreateBody", () => {
  it("sends only the destination when the optional fields are empty", () => {
    expect(buildCreateBody({ ...emptyLinkValues(), destination_url: "https://a.example" })).toEqual({
      destination_url: "https://a.example",
    });
  });

  it("trims text and leaves out whitespace-only optionals", () => {
    expect(
      buildCreateBody({ destination_url: " https://a.example ", alias: "  ", title: "  Hi  ", expires_at: "" }),
    ).toEqual({ destination_url: "https://a.example", title: "Hi" });
  });

  it("converts the local expiry to UTC", () => {
    const body = buildCreateBody({ ...emptyLinkValues(), destination_url: "https://a.example", expires_at: "2030-01-02T03:04" });
    expect(body.expires_at).toBe(new Date("2030-01-02T03:04").toISOString());
  });
});

describe("buildPatchBody", () => {
  const link = makeLink({ title: "Docs", destination_url: "https://a.example", expires_at: "2030-05-06T07:08:09Z" });

  it("is empty when nothing changed, even if the stored expiry has seconds", () => {
    expect(buildPatchBody(link, valuesFromLink(link))).toEqual({});
  });

  it("compares the destination after trimming, so stray spaces are not a change", () => {
    const values = { ...valuesFromLink(link), destination_url: "  https://a.example  " };
    expect(buildPatchBody(link, values)).toEqual({});
  });

  it("sends only the fields that changed", () => {
    const values = { ...valuesFromLink(link), destination_url: "https://b.example" };
    expect(buildPatchBody(link, values)).toEqual({ destination_url: "https://b.example" });
  });

  it("clears title and expiry with null", () => {
    const values = { ...valuesFromLink(link), title: "  ", expires_at: "" };
    expect(buildPatchBody(link, values)).toEqual({ title: null, expires_at: null });
  });

  it("sets a title on a link that had none, and an expiry on one that never expired", () => {
    const bare = makeLink({ title: null, expires_at: null });
    const values = { ...valuesFromLink(bare), title: "New", expires_at: "2031-01-01T00:00" };
    expect(buildPatchBody(bare, values)).toEqual({ title: "New", expires_at: new Date("2031-01-01T00:00").toISOString() });
  });
});

describe("datetime-local conversion", () => {
  it("round-trips a minute-precision instant", () => {
    const iso = "2030-05-06T07:08:00.000Z";
    expect(localInputToIso(isoToLocalInput(iso))).toBe(iso);
  });

  it("returns null for empty or invalid text", () => {
    expect(localInputToIso("")).toBeNull();
    expect(localInputToIso("not a date")).toBeNull();
  });
});
