// JobHub progressive enhancement. Every feature also works without JS
// (plain forms and links); this script only makes interactions smoother.
(function () {
  "use strict";

  var $ = function (sel, root) { return (root || document).querySelector(sel); };
  var $$ = function (sel, root) { return Array.prototype.slice.call((root || document).querySelectorAll(sel)); };
  var store = {
    get: function (k) { try { return localStorage.getItem(k); } catch (e) { return null; } },
    set: function (k, v) { try { localStorage.setItem(k, v); } catch (e) { /* ignore */ } },
    del: function (k) { try { localStorage.removeItem(k); } catch (e) { /* ignore */ } }
  };

  function toast(msg) {
    var el = $("#toast");
    if (!el) return;
    el.textContent = msg;
    el.hidden = false;
    clearTimeout(toast._t);
    toast._t = setTimeout(function () { el.hidden = true; }, 3500);
  }

  function post(form) {
    return fetch(form.action, {
      method: "POST",
      body: new FormData(form),
      headers: { "X-Requested-With": "fetch", "Accept": "application/json" },
      credentials: "same-origin"
    }).then(function (r) {
      return r.json().catch(function () { return {}; }).then(function (data) {
        if (!r.ok) { throw new Error(data.error || data.detail || ("Request failed (" + r.status + ")")); }
        return data;
      });
    });
  }

  // ---- Theme ----------------------------------------------------------------
  function applyTheme(pref) {
    var dark = pref === "dark" || (pref === "system" && window.matchMedia("(prefers-color-scheme: dark)").matches);
    document.documentElement.setAttribute("data-theme", dark ? "dark" : "light");
    $$("[data-theme-set]").forEach(function (b) { b.classList.toggle("is-on", b.getAttribute("data-theme-set") === pref); });
  }
  var themePref = store.get("jobhub-theme") || "system";
  applyTheme(themePref);
  var tt = $("#theme-toggle");
  if (tt) tt.addEventListener("click", function () {
    var next = document.documentElement.getAttribute("data-theme") === "dark" ? "light" : "dark";
    store.set("jobhub-theme", next); themePref = next; applyTheme(next);
  });
  $$("[data-theme-set]").forEach(function (b) {
    b.addEventListener("click", function () {
      themePref = b.getAttribute("data-theme-set"); store.set("jobhub-theme", themePref); applyTheme(themePref);
    });
  });
  if (window.matchMedia) {
    window.matchMedia("(prefers-color-scheme: dark)").addEventListener("change", function () {
      if (themePref === "system") applyTheme("system");
    });
  }

  // ---- Relative times ---------------------------------------------------------
  var rtf = window.Intl && Intl.RelativeTimeFormat ? new Intl.RelativeTimeFormat(undefined, { numeric: "auto" }) : null;
  function relative(iso) {
    var d = new Date(iso);
    if (isNaN(d)) return null;
    var diff = (d.getTime() - Date.now()) / 1000;
    var abs = Math.abs(diff);
    if (!rtf) return d.toLocaleString();
    if (abs < 45) return diff < 0 ? "just now" : "in a moment";
    if (abs < 3600) return rtf.format(Math.round(diff / 60), "minute");
    if (abs < 86400) return rtf.format(Math.round(diff / 3600), "hour");
    if (abs < 86400 * 30) return rtf.format(Math.round(diff / 86400), "day");
    return d.toLocaleDateString(undefined, { day: "numeric", month: "short", year: "numeric" });
  }
  function refreshTimes(root) {
    $$("time[datetime]", root).forEach(function (t) {
      var iso = t.getAttribute("datetime");
      var d = new Date(iso);
      if (isNaN(d)) return;
      t.title = d.toLocaleString();
      if (t.hasAttribute("data-relative")) {
        var r = relative(iso);
        if (r) t.textContent = r;
      } else {
        t.textContent = d.toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" });
      }
    });
  }
  refreshTimes();
  setInterval(function () { refreshTimes(); }, 60000);

  // ---- Sync status & "Fetch Jobs Now" ----------------------------------------
  var pill = $("#sync-pill"), pillText = $("#sync-text"), fetchBtn = $("#fetch-now");
  var bar = $("#sync-progress"), fill = $("#sync-progress-fill"), barText = $("#sync-progress-text");
  var polling = null, wasRunning = pill && pill.getAttribute("data-running") === "true";

  function renderStatus(s) {
    var run = s.run;
    if (pill) pill.setAttribute("data-running", s.running ? "true" : "false");
    if (fetchBtn) fetchBtn.disabled = !!s.running;
    if (s.running && run) {
      var pct = run.sources_total ? Math.max(5, Math.round((run.sources_done / run.sources_total) * 100)) : 5;
      if (bar) { bar.hidden = false; }
      if (fill) fill.style.width = pct + "%";
      var label = run.current_source ? ("Fetching " + run.current_source) : "Starting";
      if (barText) barText.textContent = label + " · " + run.sources_done + "/" + run.sources_total +
        " sources · " + run.added + " new, " + run.updated + " updated";
      if (pillText) pillText.textContent = "Syncing…";
    } else {
      if (bar) bar.hidden = true;
      if (pillText) {
        if (s.last_success_at) {
          pillText.innerHTML = "";
          pillText.appendChild(document.createTextNode("Synced "));
          var t = document.createElement("time");
          t.setAttribute("datetime", s.last_success_at); t.setAttribute("data-relative", "");
          pillText.appendChild(t); refreshTimes(pillText);
        } else {
          pillText.textContent = "Not synced yet";
        }
      }
      if (pill && run && run.status === "failed") pill.setAttribute("data-state", "error");
    }
  }

  function poll() {
    fetch("/api/sync/status", { headers: { "Accept": "application/json" }, credentials: "same-origin" })
      .then(function (r) { return r.json(); })
      .then(function (s) {
        renderStatus(s);
        if (s.running) {
          wasRunning = true;
          polling = setTimeout(poll, 1500);
        } else {
          polling = null;
          if (wasRunning && s.run) {
            wasRunning = false;
            var r = s.run;
            var msg = "Sync " + r.status + ": " + r.fetched + " fetched, " + r.added + " added, " + r.updated +
              " updated, " + r.skipped + " skipped, " + r.expired + " expired" + (r.errors ? ", " + r.errors + " source error(s)" : "") + ".";
            showDone(msg);
          }
        }
      })
      .catch(function () { polling = setTimeout(poll, 5000); });
  }
  function startPolling() { if (!polling) poll(); }

  function showDone(msg) {
    if (!bar) { toast(msg); return; }
    bar.hidden = false;
    if (fill) fill.style.width = "100%";
    barText.textContent = msg + " ";
    var a = document.createElement("a");
    a.href = window.location.pathname + window.location.search; a.textContent = "Reload to see changes";
    a.className = "link";
    barText.appendChild(a);
  }

  $$(".js-sync-form").forEach(function (f) {
    f.addEventListener("submit", function (e) {
      e.preventDefault();
      if (fetchBtn) fetchBtn.disabled = true;
      post(f).then(function () { wasRunning = true; startPolling(); toast("Synchronisation started"); })
        .catch(function (err) { toast(err.message); if (fetchBtn) fetchBtn.disabled = false; startPolling(); });
    });
  });
  $$(".js-source-sync").forEach(function (f) {
    f.addEventListener("submit", function (e) {
      e.preventDefault();
      post(f).then(function () { wasRunning = true; startPolling(); toast("Refresh started"); })
        .catch(function (err) { toast(err.message); });
    });
  });
  if (wasRunning) startPolling();

  // ---- Save / hide toggles ------------------------------------------------------
  $$("form.js-toggle").forEach(function (f) {
    f.addEventListener("submit", function (e) {
      e.preventDefault();
      var kind = f.getAttribute("data-kind");
      var actionInput = f.querySelector("input[name=action]");
      post(f).then(function (data) {
        var btn = f.querySelector("button");
        if (kind === "save") {
          var on = !!data.saved;
          actionInput.value = on ? "unsave" : "save";
          btn.classList.toggle("is-on", on);
          btn.setAttribute("aria-pressed", on ? "true" : "false");
          btn.title = on ? "Remove from saved" : "Save job";
          btn.setAttribute("aria-label", btn.title);
          toast(on ? "Saved" : "Removed from saved jobs");
        } else {
          var card = f.closest(".job-card");
          if (card && data.hidden) {
            card.remove();
            toast("Job hidden — restore it from Hidden jobs");
          } else if (card && !data.hidden) {
            card.remove();
            toast("Job restored");
          }
        }
      }).catch(function (err) { toast(err.message); });
    });
  });

  // ---- Apply flow -----------------------------------------------------------------
  var modal = $("#apply-modal");
  function today(offset) {
    var d = new Date(); d.setDate(d.getDate() + (offset || 0));
    return d.getFullYear() + "-" + String(d.getMonth() + 1).padStart(2, "0") + "-" + String(d.getDate()).padStart(2, "0");
  }
  $$("a[data-apply]").forEach(function (a) {
    a.addEventListener("click", function () {
      if (!modal || typeof modal.showModal !== "function") return;
      var id = a.getAttribute("data-apply");
      var form = $("#apply-modal-form");
      form.action = "/jobs/" + id + "/track";
      form.querySelector("input[name=next]").value = a.getAttribute("data-next") || ("/jobs/" + id);
      $("#apply-modal-date").value = today(0);
      $("#apply-modal-follow").value = today(7);
      // Let the browser open the new tab first, then ask.
      setTimeout(function () { modal.showModal(); }, 400);
    });
  });
  $$("[data-close-modal]").forEach(function (b) { b.addEventListener("click", function () { modal.close(); }); });

  // ---- Copy buttons ------------------------------------------------------------------
  $$("[data-copy]").forEach(function (b) {
    b.addEventListener("click", function () {
      var el = document.getElementById(b.getAttribute("data-copy"));
      if (!el) return;
      var text = el.value || el.textContent;
      (navigator.clipboard ? navigator.clipboard.writeText(text) : Promise.reject())
        .then(function () { toast("Copied to clipboard"); })
        .catch(function () { el.select(); document.execCommand("copy"); toast("Copied"); });
    });
  });

  // ---- Confirmations ------------------------------------------------------------------
  $$("[data-confirm]").forEach(function (el) {
    el.addEventListener("click", function (e) {
      if (!window.confirm(el.getAttribute("data-confirm"))) { e.preventDefault(); e.stopImmediatePropagation(); }
    });
  });

  // ---- Auto-submit selects & range outputs ---------------------------------------------
  $$("select[data-autosubmit]").forEach(function (s) { s.addEventListener("change", function () { s.form.submit(); }); });
  $$("input[type=range][data-output]").forEach(function (r) {
    var out = document.getElementById(r.getAttribute("data-output"));
    r.addEventListener("input", function () { if (out) out.textContent = r.value; });
  });

  // ---- Country combobox search ------------------------------------------------------------
  $$(".combo").forEach(function (combo) {
    var input = $(".combo-search", combo);
    var label = $(".combo-label", combo);
    if (input) input.addEventListener("input", function () {
      var q = input.value.trim().toLowerCase();
      $$(".combo-list .check", combo).forEach(function (row) {
        var name = row.getAttribute("data-name") || row.textContent.toLowerCase();
        row.hidden = q !== "" && name.indexOf(q) === -1;
      });
      $$(".combo-group-label", combo).forEach(function (g) { g.hidden = q !== ""; });
    });
    if (input) input.addEventListener("keydown", function (e) { if (e.key === "Enter") e.preventDefault(); });
    combo.addEventListener("change", function () {
      var checked = $$("input[type=checkbox]:checked", combo);
      // keep duplicate (popular + all) checkboxes in sync
      if (label) label.textContent = checked.length ? checked.length + " selected" : "All countries";
    });
  });

  // ---- Collapse the filter panel on small screens -------------------------------------------
  if (window.matchMedia && window.matchMedia("(max-width: 1080px)").matches) {
    $$("details.filters-toggle").forEach(function (d) { d.removeAttribute("open"); });
  }

  // ---- Filter persistence (Discover) --------------------------------------------------------
  var persist = $("[data-persist-filters]");
  if (persist) {
    var key = "jobhub-filters-" + persist.getAttribute("data-persist-filters");
    if (persist.getAttribute("data-reset") === "1") {
      store.del(key);
      if (window.history && history.replaceState) history.replaceState(null, "", window.location.pathname);
    } else if (!window.location.search) {
      var saved = store.get(key);
      if (saved && saved.length > 1) { window.location.replace(window.location.pathname + "?" + saved); return; }
    } else {
      var params = new URLSearchParams(window.location.search);
      params.delete("page"); params.delete("notice"); params.delete("error");
      store.set(key, params.toString());
    }
    // Remove empty fields so URLs stay short.
    var ff = $("#filter-form");
    if (ff) ff.addEventListener("submit", function () {
      $$("input, select", ff).forEach(function (el) {
        if ((el.type === "text" || el.type === "search" || el.type === "number" || el.tagName === "SELECT") && !el.value) el.disabled = true;
        if (el.type === "range" && el.value === "0") el.disabled = true;
      });
    });
  }

  // ---- Alert quick-fill ----------------------------------------------------------------------
  $$("[data-fill-query]").forEach(function (b) {
    b.addEventListener("click", function () {
      var input = b.closest("form").querySelector("input[name=query]");
      if (input) input.value = b.getAttribute("data-fill-query");
    });
  });

  // ---- Resume drop zone -------------------------------------------------------------------------
  $$(".dropzone").forEach(function (z) {
    ["dragenter", "dragover"].forEach(function (ev) { z.addEventListener(ev, function (e) { e.preventDefault(); z.classList.add("is-drag"); }); });
    ["dragleave", "drop"].forEach(function (ev) { z.addEventListener(ev, function () { z.classList.remove("is-drag"); }); });
    z.addEventListener("drop", function (e) {
      e.preventDefault();
      var input = z.querySelector("input[type=file]");
      if (input && e.dataTransfer.files.length) input.files = e.dataTransfer.files;
    });
  });

  // ---- Close mobile nav on navigation ---------------------------------------------------------------
  var navToggle = $("#nav-toggle");
  document.addEventListener("keydown", function (e) { if (e.key === "Escape" && navToggle) navToggle.checked = false; });
})();
