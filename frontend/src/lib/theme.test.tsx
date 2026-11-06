import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { ThemeToggle } from "@/components/layout/ThemeToggle";
import { applyTheme, resolveTheme, THEME_KEY } from "./theme";

function osPrefers(dark: boolean) {
  vi.spyOn(window, "matchMedia").mockImplementation(
    (query: string) => ({ matches: dark && query.includes("dark"), media: query }) as MediaQueryList,
  );
}

describe("theme", () => {
  it("uses the stored choice over the operating system", () => {
    osPrefers(true);
    localStorage.setItem(THEME_KEY, "light");
    expect(resolveTheme()).toBe("light");
  });

  it("falls back to the operating system, and ignores a junk stored value", () => {
    osPrefers(true);
    expect(resolveTheme()).toBe("dark");
    localStorage.setItem(THEME_KEY, "purple");
    expect(resolveTheme()).toBe("dark");
    osPrefers(false);
    expect(resolveTheme()).toBe("light");
  });

  it("applyTheme toggles the dark class on <html>", () => {
    applyTheme("dark");
    expect(document.documentElement).toHaveClass("dark");
    applyTheme("light");
    expect(document.documentElement).not.toHaveClass("dark");
  });

  it("the toggle flips the class, names the next theme, and remembers the choice", async () => {
    osPrefers(false);
    applyTheme("light");
    render(<ThemeToggle />);

    await userEvent.click(screen.getByRole("button", { name: "Switch to dark theme" }));
    expect(document.documentElement).toHaveClass("dark");
    expect(localStorage.getItem(THEME_KEY)).toBe("dark");

    await userEvent.click(screen.getByRole("button", { name: "Switch to light theme" }));
    expect(document.documentElement).not.toHaveClass("dark");
    expect(localStorage.getItem(THEME_KEY)).toBe("light");
  });
});
