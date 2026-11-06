import { screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import { server } from "@/test/server";
import { meSignedIn, meSignedOut, renderApp } from "@/test/utils";

describe("page titles", () => {
  it.each([
    ["/", "Short links with click counts · LinkForge", "signedOut"],
    ["/login", "Sign in · LinkForge", "signedOut"],
    ["/register", "Create your account · LinkForge", "signedOut"],
    ["/app", "Dashboard · LinkForge", "signedIn"],
    ["/app/links", "Links · LinkForge", "signedIn"],
    ["/app/settings", "Settings · LinkForge", "signedIn"],
    ["/nowhere", "Page not found · LinkForge", "none"],
  ])("%s -> %s", async (path, title, who) => {
    if (who === "signedOut") server.use(meSignedOut());
    if (who === "signedIn") server.use(meSignedIn());
    renderApp(path);
    await screen.findAllByRole("heading");
    expect(document.title).toBe(title);
  });
});

describe("skip link", () => {
  it("is the first thing a keyboard user reaches in the app and points at the main content", async () => {
    server.use(meSignedIn());
    renderApp("/app");
    await screen.findByRole("heading", { name: "Dashboard" });

    await userEvent.tab();
    const skip = screen.getByRole("link", { name: "Skip to content" });
    expect(skip).toHaveFocus();
    expect(skip).toHaveAttribute("href", "#main");
    expect(screen.getByRole("main")).toHaveAttribute("id", "main");
  });
});

describe("not found page", () => {
  it("explains and offers a way home", async () => {
    renderApp("/nowhere");
    const main = await screen.findByRole("main");
    expect(within(main).getByRole("heading", { name: "Page not found" })).toBeInTheDocument();
    expect(within(main).getByRole("link", { name: "Go to the home page" })).toHaveAttribute("href", "/");
  });
});

describe("labels", () => {
  it("stack above their control (the status filter label sits over its select)", async () => {
    server.use(meSignedIn());
    renderApp("/app/links");
    const label = await screen.findByText("Status", { selector: "label" });
    expect(label).toHaveClass("block");
  });
});
