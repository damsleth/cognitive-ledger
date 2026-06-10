/**
 * graph.js — Canvas force graph for the /graph view.
 * Depends on d3.v7.min.js loaded before this script.
 */
(function () {
  "use strict";

  const canvas = document.getElementById("graph-canvas");
  if (!canvas) return;

  const source = canvas.dataset.source;
  const tooltip = Object.assign(document.createElement("div"), {
    className: "graph-tooltip",
  });
  document.body.appendChild(tooltip);

  const TYPE_COLORS = {
    facts: "#4d3fa0",
    loops: "#e07b39",
    preferences: "#2a8a6e",
    goals: "#c0392b",
    identity: "#7f8c8d",
    inbox: "#888",
  };
  const DEFAULT_COLOR = "#aaa";

  function nodeColor(n) {
    return TYPE_COLORS[n.type] || DEFAULT_COLOR;
  }

  function nodeRadius(n) {
    return 4 + 2 * Math.sqrt(n.incoming || 0);
  }

  let allNodes = [];
  let allLinks = [];
  let visibleNodes = [];
  let visibleLinks = [];
  let activeFilter = "all";

  function applyFilter(filter) {
    activeFilter = filter;
    if (filter === "open-loops") {
      const ids = new Set(
        allNodes
          .filter((n) => n.type === "loops" && n.status === "open")
          .map((n) => n.id)
      );
      // include nodes referenced by open loops or referencing them
      allLinks.forEach((l) => {
        const sid = typeof l.source === "object" ? l.source.id : l.source;
        const tid = typeof l.target === "object" ? l.target.id : l.target;
        if (ids.has(sid)) ids.add(tid);
        if (ids.has(tid)) ids.add(sid);
      });
      visibleNodes = allNodes.filter((n) => ids.has(n.id));
      visibleLinks = allLinks.filter((l) => {
        const sid = typeof l.source === "object" ? l.source.id : l.source;
        const tid = typeof l.target === "object" ? l.target.id : l.target;
        return ids.has(sid) && ids.has(tid);
      });
    } else {
      visibleNodes = allNodes.slice();
      visibleLinks = allLinks.slice();
    }
    restartSimulation();
  }

  // Chip filter buttons
  document.querySelectorAll(".graph-toolbar .chip").forEach((btn) => {
    btn.addEventListener("click", () => {
      document
        .querySelectorAll(".graph-toolbar .chip")
        .forEach((b) => b.classList.remove("chip-active"));
      btn.classList.add("chip-active");
      applyFilter(btn.dataset.type);
    });
  });

  let simulation;
  let transform = d3.zoomIdentity;

  function resize() {
    canvas.width = canvas.offsetWidth;
    canvas.height = canvas.offsetHeight || Math.floor(window.innerHeight * 0.75);
  }

  window.addEventListener("resize", () => {
    resize();
    ticked();
  });

  function ticked() {
    const ctx = canvas.getContext("2d");
    ctx.save();
    ctx.clearRect(0, 0, canvas.width, canvas.height);
    ctx.translate(transform.x, transform.y);
    ctx.scale(transform.k, transform.k);

    // Draw links
    ctx.strokeStyle = "rgba(120,120,120,0.3)";
    ctx.lineWidth = 1 / transform.k;
    visibleLinks.forEach((l) => {
      const s = l.source, t = l.target;
      if (!s || !t || s.x == null || t.x == null) return;
      ctx.beginPath();
      ctx.moveTo(s.x, s.y);
      ctx.lineTo(t.x, t.y);
      ctx.stroke();
    });

    // Draw nodes
    visibleNodes.forEach((n) => {
      if (n.x == null) return;
      const r = nodeRadius(n);
      ctx.beginPath();
      ctx.arc(n.x, n.y, r, 0, 2 * Math.PI);
      ctx.fillStyle = nodeColor(n);
      ctx.fill();
    });

    ctx.restore();
  }

  function restartSimulation() {
    if (simulation) simulation.stop();
    simulation = d3
      .forceSimulation(visibleNodes)
      .force(
        "link",
        d3
          .forceLink(visibleLinks)
          .id((d) => d.id)
          .distance(60)
      )
      .force("charge", d3.forceManyBody().strength(-80))
      .force("center", d3.forceCenter(canvas.width / 2, canvas.height / 2))
      .force("collision", d3.forceCollide().radius((n) => nodeRadius(n) + 2))
      .on("tick", ticked);
  }

  // Zoom + pan
  const zoom = d3
    .zoom()
    .scaleExtent([0.1, 8])
    .on("zoom", (event) => {
      transform = event.transform;
      ticked();
    });
  d3.select(canvas).call(zoom);

  // Drag
  let dragNode = null;
  let dragOffsetX = 0, dragOffsetY = 0;

  function canvasPoint(event) {
    const rect = canvas.getBoundingClientRect();
    const cx = (event.clientX - rect.left - transform.x) / transform.k;
    const cy = (event.clientY - rect.top - transform.y) / transform.k;
    return [cx, cy];
  }

  function findNode(event) {
    const [cx, cy] = canvasPoint(event);
    let best = null, bestDist = Infinity;
    visibleNodes.forEach((n) => {
      const dx = n.x - cx, dy = n.y - cy;
      const dist = Math.sqrt(dx * dx + dy * dy);
      if (dist < nodeRadius(n) + 2 && dist < bestDist) {
        best = n; bestDist = dist;
      }
    });
    return best;
  }

  canvas.addEventListener("mousemove", (e) => {
    const n = findNode(e);
    if (n) {
      tooltip.textContent = n.title;
      tooltip.style.left = e.pageX + 12 + "px";
      tooltip.style.top = e.pageY - 24 + "px";
      tooltip.style.display = "block";
    } else {
      tooltip.style.display = "none";
    }
  });

  canvas.addEventListener("mouseleave", () => {
    tooltip.style.display = "none";
  });

  canvas.addEventListener("click", (e) => {
    const n = findNode(e);
    if (n) window.location = "/note/" + n.id;
  });

  // Load data and start
  resize();
  fetch(source)
    .then((r) => r.json())
    .then((data) => {
      allNodes = data.nodes || [];
      allLinks = data.links || [];
      applyFilter("all");
    })
    .catch(console.error);
})();
