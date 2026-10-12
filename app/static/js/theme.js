// Applies the saved theme before first paint to avoid a flash. Preference is
// stored in localStorage ("light" | "dark" | "system").
(function () {
  var pref = "system";
  try { pref = localStorage.getItem("jobhub-theme") || "system"; } catch (e) { /* storage disabled */ }
  var dark = pref === "dark" || (pref === "system" && window.matchMedia && window.matchMedia("(prefers-color-scheme: dark)").matches);
  document.documentElement.setAttribute("data-theme", dark ? "dark" : "light");
})();
