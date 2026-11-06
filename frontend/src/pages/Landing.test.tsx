import { screen, within } from "@testing-library/react";
import { http } from "msw";
import { describe, expect, it } from "vitest";
import { server } from "@/test/server";
import { API, apiError, meSignedIn, meSignedOut, renderApp } from "@/test/utils";

describe("Landing", () => {
  it("leads with the thesis and offers account and sign-in to a visitor", async () => {
    server.use(meSignedOut());
    renderApp("/");

    expect(
      await screen.findByRole("heading", {
        level: 1,
        name: "Your link redirects first. The counting happens after.",
      }),
    ).toBeInTheDocument();
    const main = screen.getByRole("main");
    expect(within(main).getByRole("link", { name: "Create an account" })).toHaveAttribute("href", "/register");
    expect(within(main).getByRole("link", { name: "Sign in" })).toHaveAttribute("href", "/login");
  });

  it("draws the click route, labelled as an example, with real steps in order", async () => {
    server.use(meSignedOut());
    renderApp("/");
    const figure = await screen.findByRole("figure", { name: "How one click travels" });
    expect(within(figure).getByText("Example, not live data")).toBeInTheDocument();

    const lanes = within(figure).getAllByRole("list");
    expect(lanes).toHaveLength(2);
    expect(within(lanes[0]!).getAllByRole("listitem").map((li) => li.querySelector("p")?.textContent)).toEqual([
      "A visitor opens the link",
      "LinkForge answers",
      "The destination loads",
    ]);
    expect(within(lanes[1]!).getAllByRole("listitem").map((li) => li.querySelector("p")?.textContent)).toEqual([
      "The click is queued",
      "A worker stores it",
      "Your totals update",
    ]);
  });

  it("lists three features and the honest footer note, and shows no prices or fake numbers", async () => {
    server.use(meSignedOut());
    renderApp("/");
    const features = await screen.findByRole("region", { name: "Features" });
    expect(within(features).getAllByRole("heading", { level: 2 })).toHaveLength(3);
    expect(screen.getByRole("contentinfo")).toHaveTextContent("Counts can undercount during outages. Days are UTC.");
    expect(document.body.textContent).not.toMatch(/[$€£]\s?\d|pricing/i);
  });

  it("offers the dashboard instead of sign-in when already signed in", async () => {
    server.use(meSignedIn());
    renderApp("/");
    const link = await screen.findByRole("link", { name: "Open your dashboard" });
    expect(link).toHaveAttribute("href", "/app");
    expect(screen.queryByRole("link", { name: "Create an account" })).not.toBeInTheDocument();
  });

  it("still offers sign-in when the session check fails (it is not a reason to hide the page)", async () => {
    server.use(http.get(`${API}/auth/me`, () => apiError(503, "unavailable")));
    renderApp("/");
    expect(await screen.findByRole("link", { name: "Create an account" })).toBeInTheDocument();
  });
});
