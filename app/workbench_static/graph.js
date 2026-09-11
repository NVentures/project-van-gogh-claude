/* The Brain: the vault's wikilink graph, drawn on canvas.

   DESIGN.md is explicit that this reads like a plate torn out of a notebook,
   not a network-security dashboard. Two inks only: a note is drawn in --text,
   and a note touched in the last seven days is drawn at full strength while an
   older one fades back, so recency reads as fresh ink rather than as a second
   color. The current query's hits are marked in vermilion, the way you would
   circle entries in a ledger. Hairline edges at 8% opacity. No glow.

   Chrome yellow is deliberately absent here. DESIGN.md's Brain section once
   assigned --lamp to recent notes, which contradicts its own anti-slop rule 7
   (--lamp means staged and awaiting your nod, and appears nowhere else). On a
   real vault that painted most of the graph yellow and the scarcity died. Rule
   7 wins; the decision is recorded in DESIGN.md.

   The layout is hand-rolled rather than pulled from a library because the
   server must work with no network and the plugin ships no CDN assets. Naive
   all-pairs repulsion is O(n^2), which stalls on a real vault, so repulsion is
   bucketed into a uniform grid and only nearby cells are compared.
*/
window.VGBrain = (function () {
  "use strict";

  // These are derived from the node count in tune(), not fixed: a 17-note vault
  // and a 762-note vault need different physics, and constants picked on the
  // small one collapse the big one into an unreadable blob.
  var TICKS = 300;
  var REPULSION = 900;
  var SPRING = 0.006;
  var SPRING_LEN = 46;
  var GRAVITY = 0.012;
  var DAMPING = 0.86;
  var CELL = 70;              // repulsion cutoff, and the grid cell size
  var MAX_R = 9;              // a hub with 300 links must not become a planet

  function tune(n, edgeCount) {
    // Area per node grows linearly with n, so lengths grow with its root.
    var scale = Math.max(1, Math.sqrt(n / 40));
    SPRING_LEN = 46 * scale;
    CELL = 70 * scale;
    REPULSION = 900 * scale;
    // A denser graph needs weaker springs or every edge pulls it back to a dot.
    SPRING = 0.006 / Math.max(1, Math.sqrt(edgeCount / Math.max(n, 1)) / 2);
    GRAVITY = 0.012 / scale;
    TICKS = Math.min(700, 300 + Math.floor(n / 3));
  }

  var canvas = null, ctx = null;
  var nodes = [], edges = [], byId = {};
  var view = { x: 0, y: 0, k: 1 };
  var hits = {};
  var selected = null, hovered = null;
  var mounted = false, loaded = false, settled = false;
  var drag = null;

  function css(name) {
    return getComputedStyle(document.documentElement).getPropertyValue(name).trim();
  }

  // ── Layout ────────────────────────────────────────────────────────────────

  function seed(w, h) {
    // Deterministic ring seeding. A random start makes the same vault lay out
    // differently on every open, which reads as instability rather than data.
    var n = nodes.length;
    nodes.forEach(function (node, i) {
      var angle = (i / Math.max(n, 1)) * Math.PI * 2;
      var radius = (40 + (i % 17) * 9) * Math.max(1, Math.sqrt(n / 40));
      node.x = w / 2 + Math.cos(angle) * radius;
      node.y = h / 2 + Math.sin(angle) * radius;
      node.vx = 0;
      node.vy = 0;
    });
  }

  function step(w, h, alpha) {
    var grid = {}, i, n, key;
    for (i = 0; i < nodes.length; i += 1) {
      n = nodes[i];
      key = Math.floor(n.x / CELL) + ":" + Math.floor(n.y / CELL);
      (grid[key] || (grid[key] = [])).push(n);
    }

    for (i = 0; i < nodes.length; i += 1) {
      n = nodes[i];
      var cx = Math.floor(n.x / CELL), cy = Math.floor(n.y / CELL);
      for (var gx = cx - 1; gx <= cx + 1; gx += 1) {
        for (var gy = cy - 1; gy <= cy + 1; gy += 1) {
          var bucket = grid[gx + ":" + gy];
          if (!bucket) continue;
          for (var b = 0; b < bucket.length; b += 1) {
            var m = bucket[b];
            if (m === n) continue;
            var dx = n.x - m.x, dy = n.y - m.y;
            var d2 = dx * dx + dy * dy;
            if (d2 < 0.01) { dx = (i % 7) - 3; dy = (b % 7) - 3; d2 = 9; }
            if (d2 > CELL * CELL) continue;
            // Clamped: two notes that land almost on top of each other would
            // otherwise fling each other off the plate in a single step.
            var force = Math.min(REPULSION / d2, 30);
            var d = Math.sqrt(d2);
            n.vx += (dx / d) * force * alpha;
            n.vy += (dy / d) * force * alpha;
          }
        }
      }
    }

    for (i = 0; i < edges.length; i += 1) {
      var s = byId[edges[i].s], t = byId[edges[i].t];
      if (!s || !t) continue;
      var ex = t.x - s.x, ey = t.y - s.y;
      var len = Math.sqrt(ex * ex + ey * ey) || 1;
      var pull = (len - SPRING_LEN) * SPRING * alpha;
      var ux = (ex / len) * pull, uy = (ey / len) * pull;
      s.vx += ux; s.vy += uy;
      t.vx -= ux; t.vy -= uy;
    }

    for (i = 0; i < nodes.length; i += 1) {
      n = nodes[i];
      n.vx += (w / 2 - n.x) * GRAVITY * alpha;
      n.vy += (h / 2 - n.y) * GRAVITY * alpha;
      n.vx *= DAMPING;
      n.vy *= DAMPING;
      // A single runaway node would blow out the bounding box and shrink the
      // whole plate to a dot when fit() runs.
      n.vx = Math.max(-60, Math.min(60, n.vx)) || 0;
      n.vy = Math.max(-60, Math.min(60, n.vy)) || 0;
      n.x += n.vx;
      n.y += n.vy;
    }
  }

  function fit() {
    if (!nodes.length) return;
    var w = canvas.clientWidth, h = canvas.clientHeight;
    var minX = Infinity, minY = Infinity, maxX = -Infinity, maxY = -Infinity;
    nodes.forEach(function (n) {
      minX = Math.min(minX, n.x); maxX = Math.max(maxX, n.x);
      minY = Math.min(minY, n.y); maxY = Math.max(maxY, n.y);
    });
    if (!isFinite(minX) || !isFinite(maxX) || !isFinite(minY) || !isFinite(maxY)) return;
    var pad = 24;
    var k = Math.min((w - pad * 2) / Math.max(maxX - minX, 1),
                     (h - pad * 2) / Math.max(maxY - minY, 1));
    view.k = Math.max(0.08, Math.min(2.5, k));
    view.x = w / 2 - ((minX + maxX) / 2) * view.k;
    view.y = h / 2 - ((minY + maxY) / 2) * view.k;
  }

  function settle() {
    var w = canvas.width / (window.devicePixelRatio || 1);
    var h = canvas.height / (window.devicePixelRatio || 1);
    tune(nodes.length, edges.length);
    seed(w, h);
    for (var i = 0; i < TICKS; i += 1) {
      step(w, h, 1 - i / TICKS);
    }
    fit();
    settled = true;
  }

  // ── Painting ──────────────────────────────────────────────────────────────

  function radius(node) { return Math.min(MAX_R, 1.8 + Math.sqrt(node.degree) * 1.1); }

  function paint() {
    if (!ctx) return;
    var dpr = window.devicePixelRatio || 1;
    var w = canvas.width / dpr, h = canvas.height / dpr;
    var ink = css("--text") || "#1C1A16";
    var vermilion = css("--vermilion") || "#A8321A";
    var surface = css("--surface") || "#FBF8F1";

    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.fillStyle = surface;
    ctx.fillRect(0, 0, w, h);
    ctx.translate(view.x, view.y);
    ctx.scale(view.k, view.k);

    ctx.strokeStyle = ink;
    ctx.globalAlpha = Math.max(0.022, Math.min(0.08, 400 / Math.max(edges.length, 1)));
    ctx.lineWidth = 1 / view.k;
    ctx.beginPath();
    for (var i = 0; i < edges.length; i += 1) {
      var s = byId[edges[i].s], t = byId[edges[i].t];
      if (!s || !t) continue;
      ctx.moveTo(s.x, s.y);
      ctx.lineTo(t.x, t.y);
    }
    ctx.stroke();
    ctx.globalAlpha = 1;

    for (i = 0; i < nodes.length; i += 1) {
      var n = nodes[i];
      ctx.fillStyle = hits[n.id] ? vermilion : ink;
      ctx.globalAlpha = hits[n.id] ? 1 : (n.recent ? 1 : 0.42);
      ctx.beginPath();
      ctx.arc(n.x, n.y, radius(n), 0, Math.PI * 2);
      ctx.fill();
      ctx.globalAlpha = 1;
    }

    var label = hovered || selected;
    if (label && byId[label]) {
      var node = byId[label];
      ctx.font = (12 / view.k) + "px " + (css("--font-mono") || "monospace");
      ctx.fillStyle = ink;
      ctx.textAlign = "center";
      ctx.fillText(node.label, node.x, node.y - radius(node) - 5 / view.k);
    }
  }

  // ── Interaction ───────────────────────────────────────────────────────────

  function toWorld(ev) {
    var rect = canvas.getBoundingClientRect();
    return {
      x: (ev.clientX - rect.left - view.x) / view.k,
      y: (ev.clientY - rect.top - view.y) / view.k,
    };
  }

  function nodeAt(pt) {
    for (var i = nodes.length - 1; i >= 0; i -= 1) {
      var n = nodes[i];
      var r = radius(n) + 4;
      if ((n.x - pt.x) * (n.x - pt.x) + (n.y - pt.y) * (n.y - pt.y) <= r * r) return n;
    }
    return null;
  }

  function openPanel(node) {
    var panel = document.getElementById("brain-panel");
    fetch("/api/graph/node?id=" + encodeURIComponent(node.id) + "&t="
          + encodeURIComponent(token()), { headers: { "X-Workbench-Token": token() } })
      .then(function (r) { return r.json(); })
      .then(function (d) {
        function list(items, empty) {
          if (!items.length) return '<li class="meta">' + empty + "</li>";
          return items.slice(0, 25).map(function (i) {
            return '<li><a data-node="' + i.replace(/"/g, "&quot;") + '">'
                 + i.replace(/</g, "&lt;") + "</a></li>";
          }).join("");
        }
        panel.hidden = false;
        panel.innerHTML = "<h3>" + node.label.replace(/</g, "&lt;") + "</h3>"
          + '<p class="meta">' + node.degree + " links. "
          + (node.recent ? "Touched this week." : "Not touched this week.") + "</p>"
          + '<p class="micro">LINKS OUT</p><ul>' + list(d.outbound || [], "Nothing was linked out.") + "</ul>"
          + '<p class="micro">LINKS IN</p><ul>' + list(d.inbound || [], "Nothing was linked in.") + "</ul>";
      })
      .catch(function () { panel.hidden = true; });
  }

  function token() {
    try { return sessionStorage.getItem("vg_token") || ""; } catch (e) { return ""; }
  }

  function wire() {
    canvas.addEventListener("mousedown", function (ev) {
      var pt = toWorld(ev);
      var node = nodeAt(pt);
      if (node) {
        drag = { node: node };
      } else {
        drag = { pan: true, x: ev.clientX - view.x, y: ev.clientY - view.y };
      }
    });

    window.addEventListener("mousemove", function (ev) {
      if (drag && drag.pan) {
        view.x = ev.clientX - drag.x;
        view.y = ev.clientY - drag.y;
        paint();
        return;
      }
      if (drag && drag.node) {
        var pt = toWorld(ev);
        drag.node.x = pt.x;
        drag.node.y = pt.y;
        drag.moved = true;
        paint();
        return;
      }
      if (!mounted) return;
      var over = nodeAt(toWorld(ev));
      var id = over ? over.id : null;
      if (id !== hovered) {
        hovered = id;
        canvas.style.cursor = id ? "pointer" : "default";
        paint();
      }
    });

    window.addEventListener("mouseup", function () {
      if (drag && drag.node && !drag.moved) {
        selected = drag.node.id;
        openPanel(drag.node);
        paint();
      }
      drag = null;
    });

    canvas.addEventListener("wheel", function (ev) {
      ev.preventDefault();
      var rect = canvas.getBoundingClientRect();
      var mx = ev.clientX - rect.left, my = ev.clientY - rect.top;
      var factor = ev.deltaY < 0 ? 1.12 : 1 / 1.12;
      var next = Math.max(0.25, Math.min(4, view.k * factor));
      view.x = mx - (mx - view.x) * (next / view.k);
      view.y = my - (my - view.y) * (next / view.k);
      view.k = next;
      paint();
    }, { passive: false });

    document.addEventListener("click", function (ev) {
      var link = ev.target.closest("[data-node]");
      if (!link) return;
      var node = byId[link.dataset.node];
      if (node) {
        selected = node.id;
        view.x = canvas.clientWidth / 2 - node.x * view.k;
        view.y = canvas.clientHeight / 2 - node.y * view.k;
        openPanel(node);
        paint();
      }
    });

    var query = document.getElementById("brain-query");
    if (query) {
      query.addEventListener("input", function () {
        var term = this.value.trim().toLowerCase();
        hits = {};
        if (term) {
          var terms = term.split(/\s+/);
          nodes.forEach(function (n) {
            var hay = (n.id + " " + n.label).toLowerCase();
            if (terms.every(function (t) { return hay.indexOf(t) !== -1; })) hits[n.id] = true;
          });
        }
        var count = Object.keys(hits).length;
        document.getElementById("brain-meta").textContent = term
          ? (count ? count + (count === 1 ? " note matched." : " notes matched.")
                   : "Nothing matched.")
          : nodes.length + " notes, " + edges.length + " links were drawn.";
        paint();
      });
    }
  }

  // ── Public ────────────────────────────────────────────────────────────────

  function resize() {
    if (!canvas) return;
    var dpr = window.devicePixelRatio || 1;
    canvas.width = canvas.clientWidth * dpr;
    canvas.height = canvas.clientHeight * dpr;
    paint();
  }

  function mount() {
    if (mounted) { resize(); return; }
    canvas = document.getElementById("brain-canvas");
    if (!canvas) return;
    ctx = canvas.getContext("2d");
    mounted = true;
    wire();
    resize();

    if (loaded) { if (!settled) settle(); paint(); return; }
    fetch("/api/graph", { headers: { "X-Workbench-Token": token() } })
      .then(function (r) { return r.json(); })
      .then(function (g) {
        nodes = (g.nodes || []).map(function (n) { return Object.assign({}, n); });
        edges = g.edges || [];
        byId = {};
        nodes.forEach(function (n) { byId[n.id] = n; });
        loaded = true;
        settle();
        paint();
        var meta = document.getElementById("brain-meta");
        if (meta) {
          meta.textContent = g.error
            ? "The vault was not found."
            : nodes.length + " notes, " + edges.length + " links were drawn.";
        }
      })
      .catch(function () {
        var meta = document.getElementById("brain-meta");
        if (meta) meta.textContent = "The graph did not load.";
      });
  }

  return {
    mount: mount,
    resize: resize,
    repaint: paint,
    noteCount: function () { return nodes.length; },
  };
})();
