// Runs before first paint. Mirrors src/lib/theme.ts: the stored choice wins, otherwise the
// operating system preference. localStorage holds ONLY this one string (never a token).
(function () {
  try {
    var stored = localStorage.getItem("lf-theme");
    var dark =
      stored === "dark" ||
      (stored !== "light" && window.matchMedia("(prefers-color-scheme: dark)").matches);
    if (dark) document.documentElement.classList.add("dark");
  } catch (_) {
    /* storage blocked: stay light */
  }
})();
