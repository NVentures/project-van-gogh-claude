/* Van Gogh Workbench.

   Plain ES6, zero dependencies, no build step. The server is on localhost and
   there is exactly one user, so this polls rather than holding a socket open.

   Voice rule from DESIGN.md, enforced by hand in every string below: the
   product speaks in completed aspect ("Drafted at 03:40. Waiting on you."), and
   the only present-tense imperatives anywhere are SEND and KICK OFF, because
   they are the only two things a human must do.
*/
(function () {
  "use strict";

  // ── Session token ─────────────────────────────────────────────────────────
  // The launcher opens /?t=<token>. Stash it, then strip it from the address
  // bar so the token does not sit in history or get copied into a paste.
  var params = new URLSearchParams(location.search);
  var urlToken = params.get("t");
  if (urlToken) {
    try { sessionStorage.setItem("vg_token", urlToken); } catch (e) { /* private window */ }
    history.replaceState(null, "", location.pathname);
  }
  var TOKEN = urlToken || (function () {
    try { return sessionStorage.getItem("vg_token") || ""; } catch (e) { return ""; }
  })();

  function api(path, options) {
    var opts = options || {};
    opts.headers = Object.assign({ "X-Workbench-Token": TOKEN }, opts.headers || {});
    if (opts.body && typeof opts.body !== "string") {
      opts.headers["Content-Type"] = "application/json";
      opts.body = JSON.stringify(opts.body);
    }
    return fetch(path, opts).then(function (r) {
      return r.json().then(function (body) {
        if (!r.ok) throw new Error(body.error || ("http " + r.status));
        return body;
      });
    });
  }

  // ── Helpers ───────────────────────────────────────────────────────────────

  var $ = function (id) { return document.getElementById(id); };

  function esc(s) {
    return String(s === null || s === undefined ? "" : s)
      .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;").replace(/'/g, "&#39;");
  }

  var NIL = "&#183;";

  function capital(s) { return s.charAt(0).toUpperCase() + s.slice(1); }

  function clock(iso) {
    if (!iso) return "";
    var d = new Date(iso);
    if (isNaN(d)) return "";
    return String(d.getHours()).padStart(2, "0") + ":" + String(d.getMinutes()).padStart(2, "0");
  }

  function whenWords(iso) {
    if (!iso) return "never written";
    var d = new Date(iso);
    if (isNaN(d)) return "never written";
    var mins = Math.round((Date.now() - d.getTime()) / 60000);
    if (mins < 2) return "written just now";
    if (mins < 60) return "written " + mins + " minutes ago";
    var hrs = Math.round(mins / 60);
    if (hrs < 24) return "written " + hrs + (hrs === 1 ? " hour ago" : " hours ago");
    var days = Math.round(hrs / 24);
    return "written " + days + (days === 1 ? " day ago" : " days ago");
  }

  function plural(n, one, many) { return n + " " + (n === 1 ? one : many); }

  var WORDS = ["no", "one", "two", "three", "four", "five",
               "six", "seven", "eight", "nine"];

  function spelled(n, one, many) {
    var word = n < WORDS.length ? WORDS[n] : String(n);
    return word + " " + (n === 1 ? one : many);
  }

  // ── State ─────────────────────────────────────────────────────────────────

  var state = {
    meta: null,
    briefings: [],
    activeBriefing: "morning-coffee",
    tickets: [],
    counts: {},
    jobs: [],
    crm: [],
    section: "briefings",
  };

  // ── The night gutter ──────────────────────────────────────────────────────
  // A 24-hour ruler. Hours where work actually landed carry a lamp tick, which
  // is the overnight-work thesis rendered as architecture instead of copy.

  function drawGutter() {
    var g = $("gutter");
    var vertical = window.innerWidth > 940;
    var lit = {};
    state.tickets.forEach(function (t) {
      var at = (t.history && t.history.length) ? t.history[t.history.length - 1].at : t.created_at;
      var d = new Date(at);
      if (!isNaN(d)) lit[d.getHours()] = true;
    });

    var html = "";
    for (var h = 0; h < 24; h += 1) {
      var pct = (h / 24) * 100;
      var isLit = !!lit[h];
      if (vertical) {
        html += '<div class="tick' + (isLit ? " lit" : "") + '" style="top:' + pct + '%"></div>';
        if (h % 3 === 0) {
          html += '<div class="tick-label' + (isLit ? " lit" : "") + '" style="top:' + pct + '%">'
                + String(h).padStart(2, "0") + "</div>";
        }
      } else {
        html += '<div class="tick' + (isLit ? " lit" : "") + '" style="left:' + pct
              + '%;top:0;bottom:0;right:auto;width:1px;height:auto"></div>';
      }
    }
    var now = new Date();
    var nowPct = ((now.getHours() * 60 + now.getMinutes()) / 1440) * 100;
    html += vertical
      ? '<div class="gutter-now" style="top:' + nowPct + '%"></div>'
      : '<div class="gutter-now" style="left:' + nowPct + '%;top:0;bottom:0;right:auto;width:1px;height:auto"></div>';
    g.innerHTML = html;
  }

  // ── Section bar ───────────────────────────────────────────────────────────

  function moveUnderline() {
    var active = document.querySelector(".section.is-active");
    var bar = $("section-underline");
    if (!active || !bar) return;
    bar.style.width = active.offsetWidth + "px";
    bar.style.transform = "translateX(" + active.offsetLeft + "px)";
  }

  function showSection(name) {
    state.section = name;
    document.querySelectorAll(".section").forEach(function (b) {
      b.classList.toggle("is-active", b.dataset.section === name);
    });
    document.querySelectorAll(".view").forEach(function (v) {
      v.hidden = v.dataset.view !== name;
    });
    moveUnderline();
    if (name === "brain") window.VGBrain.mount();
  }

  function setCounts() {
    var c = state.counts || {};
    var staged = (c.staged || 0);
    var open = state.tickets.filter(function (t) {
      return t.state !== "delivered" && t.state !== "rated" && t.state !== "dismissed";
    }).length;
    var running = state.jobs.filter(function (j) { return j.state === "running"; }).length;
    var map = {
      briefings: state.briefings.filter(function (b) { return b.exists; }).length,
      tickets: open,
      progress: running,
      brain: window.VGBrain.noteCount(),
      inbox: state.crm.length,
    };
    Object.keys(map).forEach(function (k) {
      var el = document.querySelector('[data-count="' + k + '"]');
      if (el) el.textContent = map[k] ? map[k] : "";
    });
    return staged;
  }

  // ── Masthead ──────────────────────────────────────────────────────────────

  function drawMasthead() {
    var d = new Date();
    $("stamp-date").textContent = d.toLocaleDateString(undefined, {
      weekday: "long", month: "long", day: "numeric",
    });

    var name = (state.meta && state.meta.first_name) || "";
    var staged = state.tickets.filter(function (t) { return t.state === "staged"; }).length;
    var proposed = state.tickets.filter(function (t) { return t.state === "proposed"; }).length;
    var hour = d.getHours();
    var greeting = hour < 12 ? "Good morning" : (hour < 18 ? "Good afternoon" : "Good evening");
    var head = name ? greeting + ", " + name + "." : greeting + ".";

    var tail;
    if (staged) {
      tail = capital(spelled(staged, "thing was", "things were"))
           + " finished and left for you.";
    } else if (proposed) {
      tail = capital(spelled(proposed, "thread was", "threads were"))
           + " pulled out of the mail.";
    } else {
      tail = "The desk was clear.";
    }
    $("sentence").textContent = head + " " + tail;

    var fresh = state.briefings.filter(function (b) { return b.exists && !b.stale; }).length;
    var legacy = state.briefings.filter(function (b) { return b.legacy; }).length;
    $("subsentence").textContent = legacy
      ? capital(spelled(legacy, "briefing came", "briefings came")) + " from your older setup, read straight out of the vault."
      : (fresh
         ? capital(spelled(fresh, "briefing was", "briefings were")) + " current as of this morning."
         : "No briefing was current. Refresh from the desk on the right.");
  }

  // ── Briefings ─────────────────────────────────────────────────────────────

  function drawBriefings() {
    var picker = $("briefing-picker");
    picker.innerHTML = state.briefings.map(function (b) {
      return '<button class="pick' + (b.name === state.activeBriefing ? " is-active" : "")
           + (b.stale ? " is-stale" : "") + '" data-name="' + esc(b.name) + '" type="button">'
           + esc(b.title) + '<span class="when">' + esc(whenWords(b.generated_at)) + "</span></button>";
    }).join("");

    var current = state.briefings.filter(function (b) { return b.name === state.activeBriefing; })[0];
    var body = $("briefing-body");
    if (!current || !current.exists) {
      body.innerHTML = '<p class="empty">Nothing was written here yet. Refresh it from the desk on the right.</p>';
      return;
    }
    body.innerHTML = current.html || '<p class="empty">This briefing was empty.</p>';
  }

  // ── Tickets ───────────────────────────────────────────────────────────────

  var STATE_WORD = {
    proposed: "PROPOSED", staging: "DRAFTING", staged: "DRAFTED",
    approved: "APPROVED", running: "BUILDING", verifying: "CHECKING",
    delivered: "DELIVERED", rated: "RATED", failed: "DIED", dismissed: "DISMISSED",
  };

  function ticketNote(t) {
    var ctx = t.context || {};
    if (t.state === "failed") return "Died on the last attempt. Nothing was sent.";
    if (t.state === "delivered") return "Delivered. Rate it so the next one lands closer.";
    if (t.state === "staged") return "Drafted and waiting on you.";
    if (t.state === "approved") return "Approved. The build path was not wired up yet.";
    var bits = [];
    if (ctx.age_days) bits.push(plural(ctx.age_days, "day", "days") + " old");
    if (ctx.urgency) bits.push(ctx.urgency + " urgency");
    if (ctx.summary) bits.push(ctx.summary);
    return bits.join(". ") || NIL;
  }

  function ticketRow(t) {
    var at = (t.history && t.history.length) ? t.history[t.history.length - 1].at : t.created_at;
    return '<div class="row' + (t.state === "staged" ? " is-staged" : "") + '" data-id="' + esc(t.id) + '">'
      + '<div class="row-time">' + esc(clock(at)) + "</div>"
      + '<div class="row-main">'
      +   '<p class="row-title">' + esc(t.title) + "</p>"
      +   '<p class="row-note">' + esc(ticketNote(t)) + "</p>"
      +   (t.dod ? '<p class="row-note">Done when: ' + esc(t.dod) + "</p>" : "")
      +   (t.state === "running" || t.state === "staging"
            ? '<div class="progress-rule"><span></span></div>' : "")
      +   '<div class="row-actions">'
      +     '<button class="ghost" data-act="comment" type="button">Comment</button>'
      +     '<button class="ghost" data-act="dismiss" type="button">Dismiss</button>'
      +   "</div>"
      + "</div>"
      + '<div class="row-side">'
      +   '<span class="state ' + esc(t.state) + '">' + esc(STATE_WORD[t.state] || t.state) + "</span>"
      + "</div></div>";
  }

  function drawTickets() {
    var open = state.tickets.filter(function (t) {
      return t.state !== "delivered" && t.state !== "rated";
    });
    $("ledger").innerHTML = open.length
      ? open.map(ticketRow).join("")
      : '<p class="empty">No work was prepped. Refresh a briefing to pull threads out of the mail.</p>';

    var done = state.tickets.filter(function (t) {
      return t.state === "delivered" || t.state === "rated";
    });
    var running = state.jobs;
    $("progress-ledger").innerHTML =
      (running.length
        ? running.map(function (j) {
            return '<div class="row"><div class="row-time">' + esc(clock(j.started_at)) + "</div>"
              + '<div class="row-main"><p class="row-title">' + esc(j.label) + "</p>"
              + '<p class="row-note">' + esc(j.detail || (j.state === "running"
                  ? "Running since " + clock(j.started_at) + "." : "")) + "</p>"
              + (j.state === "running" ? '<div class="progress-rule"><span></span></div>' : "")
              + "</div><div class=\"row-side\"><span class=\"state "
              + (j.state === "done" ? "delivered" : (j.state === "failed" ? "failed" : "running"))
              + '">' + esc(j.state === "done" ? "FINISHED" : (j.state === "failed" ? "DIED" : "RUNNING"))
              + "</span></div></div>";
          }).join("")
        : "")
      + (done.length ? done.map(ticketRow).join("") : "")
      || '<p class="empty">Nothing has run yet.</p>';
  }

  // ── The nod column ────────────────────────────────────────────────────────

  function drawNod() {
    var staged = state.tickets.filter(function (t) { return t.state === "staged"; });
    var body = $("nod-body");

    body.innerHTML = staged.length
      ? staged.map(function (t) {
          return '<div class="nod-item" data-id="' + esc(t.id) + '">'
            + '<p class="nod-title">' + esc(t.title) + "</p>"
            + '<p class="meta">Drafted at ' + esc(clock(t.created_at)) + ". Waiting on you.</p>"
            + '<div class="nod-actions">'
            +   '<button class="stamp" data-act="send" type="button"><span>'
            +   (t.type === "email" ? "SEND" : "KICK OFF") + "</span></button>"
            +   '<button class="ghost" data-act="comment" type="button">Comment</button>'
            + "</div></div>";
        }).join("")
      : '<p class="empty">Nothing was left for you.</p>';

    var open = state.tickets.filter(function (t) {
      return t.state !== "delivered" && t.state !== "rated" && t.state !== "dismissed";
    }).length;
    var delivered = state.tickets.filter(function (t) {
      return t.state === "delivered" || t.state === "rated";
    }).length;

    $("deskstats").innerHTML =
        '<div class="stat"><b class="hero" style="font-size:34px">' + staged.length + "</b><span>Awaiting you</span></div>"
      + '<div class="stat"><b>' + open + "</b><span>Open</span></div>"
      + '<div class="stat"><b>' + delivered + "</b><span>Delivered</span></div>"
      + '<div class="stat"><b>' + state.crm.length + "</b><span>Counterparties</span></div>";

    var connected = !state.meta || state.meta.oauth;
    $("refreshers").innerHTML = state.briefings.map(function (b) {
      return '<button class="ghost" data-refresh="' + esc(b.name) + '" type="button"'
           + (connected ? "" : " disabled") + ">"
           + "Rewrite " + esc(b.title) + "</button>";
    }).join("")
      + (connected ? ""
         : '<p class="meta">Connect an account to rewrite these.</p>');
  }

  // ── Inbox / CRM ───────────────────────────────────────────────────────────

  function drawCrm() {
    $("crm").innerHTML = state.crm.length
      ? state.crm.map(function (g) {
          var badge = g.deal_critical
            ? '<span class="state critical">DEAL CRITICAL</span>'
            : '<span class="state proposed">' + esc(g.max_urgency.toUpperCase()) + "</span>";
          var mark = g.logo_domain
            ? '<img class="logo" src="/api/logo?domain=' + encodeURIComponent(g.logo_domain)
              + "&t=" + encodeURIComponent(TOKEN) + '" alt="" onerror="this.style.display=\'none\'">'
            : '<span class="avatar">' + esc((g.label[0] || "?").toUpperCase()) + "</span>";
          return '<div class="row"><div class="row-time">'
            + esc(plural(g.oldest_days, "day", "days")) + "</div>"
            + '<div class="row-main"><p class="row-title">' + mark + esc(g.label) + "</p>"
            + '<p class="row-note">' + esc(g.people.slice(0, 3).join(", ") || NIL)
            + ". " + esc(plural(g.count, "thread was", "threads were")) + " left open.</p>"
            + "</div>"
            + '<div class="row-side">' + badge + "</div></div>";
        }).join("")
      : '<p class="empty">No counterparty was waiting.</p>';
  }

  // ── Draw everything ───────────────────────────────────────────────────────

  function draw() {
    setCounts();
    drawMasthead();
    drawBriefings();
    drawTickets();
    drawNod();
    drawCrm();
    drawGutter();
    var v = state.meta ? state.meta.version : "";
    $("footer").textContent = "Van Gogh " + v + ". Served from your machine, on 127.0.0.1. "
      + "Nothing on this page left it.";
  }

  // ── Loading ───────────────────────────────────────────────────────────────

  function loadTickets() {
    return api("/api/tickets").then(function (d) {
      state.tickets = d.tickets || [];
      state.counts = d.counts || {};
    });
  }

  function loadJobs() {
    return api("/api/jobs").then(function (d) { state.jobs = d.jobs || []; });
  }

  function loadAll() {
    return Promise.all([
      api("/api/briefings").then(function (d) { state.briefings = d.briefings || []; }),
      loadTickets(),
      loadJobs(),
      api("/api/crm").then(function (d) { state.crm = d.groups || []; }),
    ]).then(draw);
  }

  // ── Events ────────────────────────────────────────────────────────────────

  document.addEventListener("click", function (ev) {
    var sectionBtn = ev.target.closest(".section");
    if (sectionBtn) { showSection(sectionBtn.dataset.section); return; }

    var pick = ev.target.closest(".pick");
    if (pick) { state.activeBriefing = pick.dataset.name; drawBriefings(); return; }

    var refresh = ev.target.closest("[data-refresh]");
    if (refresh) {
      var name = refresh.dataset.refresh;
      refresh.disabled = true;
      refresh.textContent = "Rewriting, this takes a minute";
      api("/api/refresh", { method: "POST", body: { briefing: name } })
        .then(function () { showSection("progress"); return loadJobs().then(draw); })
        .catch(function (e) {
          refresh.disabled = false;
          refresh.textContent = "Could not rewrite: " + e.message;
        });
      return;
    }

    var host = ev.target.closest("[data-id]");
    var act = ev.target.closest("[data-act]");
    if (!host || !act) return;
    var id = host.dataset.id;

    if (act.dataset.act === "dismiss") {
      api("/api/tickets/" + id + "/dismiss", { method: "POST", body: {} })
        .then(loadTickets).then(draw);
    } else if (act.dataset.act === "comment") {
      var text = window.prompt("What should change about this one?");
      if (text) {
        api("/api/tickets/" + id + "/comment", { method: "POST", body: { text: text } })
          .then(loadTickets).then(draw);
      }
    } else if (act.dataset.act === "send") {
      act.classList.add("is-pressed");
      api("/api/tickets/" + id + "/approve", { method: "POST", body: {} })
        .then(loadTickets).then(draw)
        .catch(function (e) {
          act.classList.remove("is-pressed");
          window.alert("Nothing was sent. " + e.message);
        });
    }
  });

  $("theme").addEventListener("click", function () {
    var root = document.documentElement;
    var now = root.getAttribute("data-theme");
    var next = now === "dark" ? "light" : "dark";
    root.setAttribute("data-theme", next);
    this.textContent = next === "dark" ? "NIGHT" : "DAY";
    try { localStorage.setItem("vg_theme", next); } catch (e) { /* blocked storage */ }
    window.VGBrain.repaint();
  });

  window.addEventListener("resize", function () { moveUnderline(); drawGutter(); window.VGBrain.resize(); });

  // ── Boot ──────────────────────────────────────────────────────────────────

  try {
    var saved = localStorage.getItem("vg_theme");
    if (saved) {
      document.documentElement.setAttribute("data-theme", saved);
      $("theme").textContent = saved === "dark" ? "NIGHT" : "DAY";
    }
  } catch (e) { /* blocked storage */ }

  api("/api/meta").then(function (meta) {
    state.meta = meta;
    // Without OAuth the page still opens: the briefings on disk and the vault
    // graph need no credential at all. Only refresh and scheduling do, and they
    // say so rather than failing when pressed.
    $("gate").hidden = !!meta.oauth;
    $("main").hidden = false;
    showSection("briefings");
    return loadAll().then(function () {
      setInterval(function () {
        var active = state.jobs.some(function (j) { return j.state === "running"; });
        loadJobs().then(function () {
          if (active) return loadTickets().then(draw);
          drawTickets();
          setCounts();
        });
      }, 2500);
    });
  }).catch(function (e) {
    $("sentence").textContent = "The desk did not answer.";
    $("subsentence").textContent = e.message;
  });
})();
