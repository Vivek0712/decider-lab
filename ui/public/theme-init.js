// Sets data-theme on <html> before the app loads, so there is no flash of the wrong theme.
(function () {
  var pref = "system";
  try {
    pref = localStorage.getItem("dl-theme") || "system";
  } catch (e) {
    /* storage blocked */
  }
  var dark = pref === "dark" || (pref !== "light" && !window.matchMedia("(prefers-color-scheme: light)").matches);
  document.documentElement.setAttribute("data-theme", dark ? "dark" : "light");
})();
