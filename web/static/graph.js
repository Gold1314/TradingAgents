/* TAGraph — shared Cytoscape renderer for the StockAgents blueprint + live graph.
 *
 * Consumes the JSON from /api/graph/blueprint (see web/graph_topology.py) and
 * exposes a small API on window.TAGraph used by index.html (live runner) and
 * blueprint.html (interactive topology + live watcher).
 */
(function (global) {
  "use strict";

  // node state -> css class (mirrored by clearNodeStates / setNodeState)
  const STATE_CLASS = {
    running: "state-running",
    done: "state-done",
    active: "state-active",
  };

  let memoryPulseTimer = null;

  // ── element construction ────────────────────────────────────────────────
  function buildElements(blueprint, opts) {
    const o = opts || {};
    const showInternal = o.showInternal !== false; // default true
    const nodes = (blueprint.nodes || []).filter((n) => showInternal || !n.internal);
    const keep = new Set(nodes.map((n) => n.id));

    const els = [];
    nodes.forEach((n) => {
      els.push({
        group: "nodes",
        data: {
          id: n.id,
          label: n.label || n.id,
          type: n.type || "node",
          stage: n.stage || "",
          color: n.color || "#94a3b8",
          internal: !!n.internal,
          meta: n.meta || {},
        },
        position: { x: Number(n.x) || 0, y: Number(n.y) || 0 },
        classes: "kind-" + (n.type || "node"),
      });
    });

    (blueprint.edges || []).forEach((e) => {
      if (!keep.has(e.source) || !keep.has(e.target)) return;
      if (!showInternal && e.internal) return;
      const classes = [];
      if (e.conditional) classes.push("edge-conditional");
      if (e.kind) classes.push("edge-" + e.kind);
      els.push({
        group: "edges",
        data: {
          id: e.id || e.source + "->" + e.target + ":" + (e.label || ""),
          source: e.source,
          target: e.target,
          label: e.label || "",
          conditional: !!e.conditional,
          kind: e.kind || "flow",
        },
        classes: classes.join(" "),
      });
    });
    return els;
  }

  // ── stylesheet ────────────────────────────────────────────────────────────
  function cytoscapeStyles() {
    return [
      {
        selector: "node",
        style: {
          "background-color": "data(color)",
          "background-opacity": 0.18,
          "border-width": 2,
          "border-color": "data(color)",
          shape: "round-rectangle",
          width: "label",
          height: 34,
          padding: "10px",
          label: "data(label)",
          color: "#e2e8f0",
          "font-size": 12,
          "font-weight": 600,
          "text-valign": "center",
          "text-halign": "center",
          "text-wrap": "wrap",
          "text-max-width": 150,
        },
      },
      {
        selector: 'node[type = "tool"], node[type = "msg_clear"], node[type = "data_vendor"]',
        style: { shape: "round-rectangle", "background-opacity": 0.1, "font-size": 10, "border-style": "dashed" },
      },
      {
        selector: 'node[type = "memory"], node[type = "identity"]',
        style: { shape: "round-tag" },
      },
      {
        selector: "edge",
        style: {
          width: 1.6,
          "line-color": "#475569",
          "target-arrow-color": "#475569",
          "target-arrow-shape": "triangle",
          "arrow-scale": 0.9,
          "curve-style": "bezier",
          label: "data(label)",
          "font-size": 8,
          color: "#64748b",
          "text-background-color": "#0b1120",
          "text-background-opacity": 0.85,
          "text-background-padding": 2,
          "text-rotation": "autorotate",
        },
      },
      { selector: "edge.edge-conditional", style: { "line-style": "dashed", "line-color": "#f59e0b", "target-arrow-color": "#f59e0b", color: "#f59e0b" } },
      { selector: "edge.edge-memory", style: { "line-color": "#a78bfa", "target-arrow-color": "#a78bfa", color: "#c4b5fd", "line-style": "dashed", width: 2 } },
      { selector: "edge.edge-data", style: { "line-color": "#fb923c", "target-arrow-color": "#fb923c", color: "#fdba74", "line-style": "dotted" } },

      // node run states
      { selector: "node.state-running", style: { "background-opacity": 0.4, "border-width": 4, "border-color": "#fbbf24", "shadow-blur": 24, "shadow-color": "#fbbf24", "shadow-opacity": 0.7 } },
      { selector: "node.state-active", style: { "background-opacity": 0.45, "border-width": 4, "border-color": "#34d399" } },
      { selector: "node.state-done", style: { "background-opacity": 0.5, "border-color": "#34d399", "border-width": 3 } },
      { selector: "node.highlighted", style: { "border-width": 5, "border-color": "#e9d5ff", "background-opacity": 0.5 } },

      // memory flow edges (animated by setMemoryPhase / _pulseEdges)
      { selector: "edge.memory-flow", style: { "line-color": "#c4b5fd", "target-arrow-color": "#c4b5fd", width: 3, "line-style": "solid" } },
      {
        selector: ".memory-flow-bright",
        style: {
          width: 5,
          "line-color": "#e9d5ff",
          "target-arrow-color": "#e9d5ff",
        },
      },
    ];
  }

  function clearMemoryFlow(cy) {
    if (memoryPulseTimer) {
      clearInterval(memoryPulseTimer);
      memoryPulseTimer = null;
    }
    if (!cy) return;
    cy.edges().removeClass("memory-flow memory-flow-bright");
    const mem = cy.getElementById("Memory Log");
    if (mem.nonempty()) mem.removeClass("state-active state-running");
  }

  function _pulseEdges(edges) {
    if (!edges || edges.length === 0) return;
    edges.addClass("memory-flow");
    let bright = true;
    memoryPulseTimer = setInterval(() => {
      bright = !bright;
      edges.toggleClass("memory-flow-bright", bright);
    }, 420);
  }

  function setMemoryPhase(cy, phase) {
    if (!cy) return;
    clearMemoryFlow(cy);

    const mem = cy.getElementById("Memory Log");
    const ident = cy.getElementById("Instrument Identity");

    // Do not touch pipeline agent node states — graph progress is driven separately.

    if (phase === "resolve") {
      if (mem.nonempty()) mem.addClass("state-active");
      return;
    }
    if (phase === "identity") {
      if (ident.nonempty()) ident.addClass("state-active");
      _pulseEdges(
        cy.edges().filter((e) => e.data("label") === "instrument_context" || e.data("label") === "pre-run")
      );
      return;
    }
    if (phase === "inject") {
      if (mem.nonempty()) mem.addClass("state-active");
      _pulseEdges(cy.edges().filter((e) => e.data("label") === "past_context"));
      return;
    }
    if (phase === "consume") {
      if (mem.nonempty()) mem.addClass("state-active");
      _pulseEdges(cy.edges().filter((e) => e.data("label") === "past_context"));
      return;
    }
    if (phase === "store") {
      if (mem.nonempty()) mem.addClass("state-active");
      _pulseEdges(cy.edges().filter((e) => e.data("label") === "store pending"));
    }
  }

  function highlightNodes(cy, nodeIds) {
    if (!cy) return;
    cy.nodes().removeClass("highlighted");
    (nodeIds || []).forEach((id) => {
      const el = cy.getElementById(id);
      if (el && !el.empty()) el.addClass("highlighted");
    });
    if (nodeIds && nodeIds.length) {
      const collection = cy.collection();
      nodeIds.forEach((id) => {
        const el = cy.getElementById(id);
        if (el && !el.empty()) collection.merge(el);
      });
      if (collection.length) cy.animate({ fit: { eles: collection, padding: 80 } }, { duration: 300 });
    }
  }

  function createGraph(container, blueprint, options) {
    const opts = options || {};
    const cy = global.cytoscape({
      container,
      elements: buildElements(blueprint, opts),
      style: cytoscapeStyles(),
      layout: { name: "preset" },
      minZoom: 0.25,
      maxZoom: 2.5,
      wheelSensitivity: 0.2,
    });

    cy.on("tap", "node", (evt) => {
      if (typeof opts.onNodeSelect === "function") {
        opts.onNodeSelect(evt.target.data(), evt.target);
      }
    });

    cy.fit(undefined, 48);
    // Container may have zero size until layout settles (common on blueprint page).
    global.requestAnimationFrame(() => {
      cy.resize();
      cy.fit(undefined, 48);
    });
    return cy;
  }

  function setNodeState(cy, nodeId, state) {
    const el = cy.getElementById(nodeId);
    if (!el || el.empty()) return;
    Object.values(STATE_CLASS).forEach((cls) => el.removeClass(cls));
    if (state && STATE_CLASS[state]) el.addClass(STATE_CLASS[state]);
  }

  function clearNodeStates(cy) {
    if (!cy) return;
    cy.nodes().forEach((n) => {
      Object.values(STATE_CLASS).forEach((cls) => n.removeClass(cls));
    });
    cy.edges().removeClass("edge-active");
    clearMemoryFlow(cy);
  }

  function pulseToolActivity(cy, toolNodeId) {
    setNodeState(cy, toolNodeId, "active");
    const agentGuess = toolNodeId.replace("tools_", "");
    const map = {
      market: "Market Analyst",
      social: "Sentiment Analyst",
      news: "News Analyst",
      fundamentals: "Fundamentals Analyst",
    };
    const agent = map[agentGuess];
    if (agent) setNodeState(cy, agent, "running");
  }

  function markAgentProgress(cy, agentNode, status) {
    setNodeState(cy, agentNode, status === "done" ? "done" : "running");
  }

  function renderDetailPanel(panelEl, nodeData) {
    if (!panelEl) return;
    const m = nodeData.meta || {};
    const tools = (m.tools || []).map((t) => `<code class="text-emerald-300">${t}</code>`).join(", ");
    const reads = (m.reads || []).map((f) => `<span class="text-sky-300">${f}</span>`).join(", ");
    const writes = (m.writes || []).map((f) => `<span class="text-amber-300">${f}</span>`).join(", ");

    panelEl.innerHTML = `
      <div class="flex items-start gap-3 mb-4">
        <div class="text-3xl">${m.icon || "•"}</div>
        <div>
          <h2 class="text-lg font-bold text-white">${nodeData.label}</h2>
          <p class="text-xs text-slate-400 mt-0.5">${m.blurb || ""}</p>
        </div>
      </div>
      <div class="space-y-3 text-sm">
        <div><span class="text-[10px] uppercase tracking-wider text-slate-500">Type</span>
          <div class="text-slate-200 mt-0.5">${nodeData.type.replace("_", " ")}</div></div>
        <div><span class="text-[10px] uppercase tracking-wider text-slate-500">Stage</span>
          <div class="text-slate-200 mt-0.5">${nodeData.stage || "—"}</div></div>
        ${m.model ? `<div><span class="text-[10px] uppercase tracking-wider text-slate-500">Model</span>
          <div class="text-slate-200 mt-0.5 font-mono text-xs">${m.model}</div></div>` : ""}
        ${m.pattern ? `<div><span class="text-[10px] uppercase tracking-wider text-slate-500">Pattern</span>
          <div class="text-slate-200 mt-0.5">${m.pattern}</div></div>` : ""}
        ${reads ? `<div><span class="text-[10px] uppercase tracking-wider text-slate-500">Reads state</span>
          <div class="mt-0.5 text-xs">${reads}</div></div>` : ""}
        ${writes ? `<div><span class="text-[10px] uppercase tracking-wider text-slate-500">Writes state</span>
          <div class="mt-0.5 text-xs">${writes}</div></div>` : ""}
        ${tools ? `<div><span class="text-[10px] uppercase tracking-wider text-slate-500">Tools</span>
          <div class="mt-0.5 text-xs">${tools}</div></div>` : ""}
        ${m.source ? `<div><span class="text-[10px] uppercase tracking-wider text-slate-500">Source</span>
          <div class="mt-0.5 font-mono text-[11px] text-slate-400 break-all">${m.source}</div></div>` : ""}
      </div>`;
  }

  function renderMemorySection(container, memory) {
    if (!container || !memory) return;
    const types = (memory.types || [])
      .map(
        (t) =>
          `<span class="inline-flex items-center gap-1 px-2 py-0.5 rounded border text-[10px]" style="border-color:${t.color}55;color:${t.color}">${t.label}</span>`
      )
      .join(" ");
    const steps = (memory.flow || [])
      .map(
        (s) => `
      <div class="memory-step flex gap-3 cursor-pointer hover:bg-slate-800/40 rounded-lg p-2 -mx-2 transition" data-nodes="${(s.graph_nodes || []).join(",")}">
        <div class="shrink-0 w-7 h-7 rounded-full bg-purple-500/20 border border-purple-500/40 flex items-center justify-center text-xs font-bold text-purple-300">${s.step}</div>
        <div class="min-w-0">
          <div class="text-[10px] uppercase tracking-wider text-purple-400/80">${s.phase}</div>
          <div class="text-sm font-semibold text-slate-200">${s.title}</div>
          <div class="text-xs text-slate-400 mt-0.5">${s.detail}</div>
          <div class="text-[10px] font-mono text-slate-500 mt-1">${s.actor}</div>
        </div>
      </div>`
      )
      .join("");
    container.innerHTML = `
      <div class="flex items-start justify-between gap-2 mb-3">
        <div>
          <h3 class="text-sm font-semibold text-purple-300">💾 ${memory.title}</h3>
          <p class="text-[11px] text-slate-500 mt-0.5">${memory.subtitle}</p>
        </div>
      </div>
      <div class="text-[10px] text-slate-500 mb-2">Storage: <code class="text-purple-300/90">${memory.storage}</code></div>
      <div class="text-[10px] text-slate-500 mb-3">Injected into: <span class="text-purple-300">${memory.consumer}</span></div>
      <div class="flex flex-wrap gap-1.5 mb-4">${types}</div>
      <div class="space-y-2">${steps}</div>
      <p class="text-[10px] text-slate-600 mt-3">Click a step to highlight related nodes on the graph.</p>`;
  }

  function renderDataSourcesSection(container, sources, toolMatrix) {
    if (!container) return;
    const cards = (sources || [])
      .map(
        (s) => `
      <div class="ds-card rounded-lg border border-slate-700 bg-slate-950/40 p-3 cursor-pointer hover:border-orange-500/50 transition" data-nodes="${(s.graph_nodes || []).join(",")}" data-id="${s.id}">
        <div class="flex items-center gap-2 mb-1">
          <span class="text-lg">${s.icon}</span>
          <span class="text-sm font-semibold text-slate-200">${s.name}</span>
          <span class="text-[10px] px-1.5 py-0.5 rounded bg-slate-800 text-slate-400">${s.kind}</span>
        </div>
        <p class="text-xs text-slate-400">${s.provides}</p>
        <p class="text-[10px] text-slate-500 mt-1.5"><span class="text-orange-400/80">How:</span> ${s.routing}</p>
        <p class="text-[10px] font-mono text-slate-600 mt-1 break-all">${s.source_file}</p>
        ${s.used_by_agents && s.used_by_agents.length ? `<p class="text-[10px] text-sky-400/80 mt-1.5">Agents: ${s.used_by_agents.join(", ")}</p>` : ""}
      </div>`
      )
      .join("");

    const matrix = (toolMatrix || [])
      .map((row) => {
        if (row.prefetch && row.prefetch.length) {
          const pf = row.prefetch
            .map((p) => `<li class="text-xs text-slate-400"><span class="text-orange-300">${p.source}</span> via ${p.via}</li>`)
            .join("");
          const tl = (row.tools || [])
            .map((t) => `<li class="text-xs text-slate-400"><code class="text-emerald-300">${t.tool}</code> → ${(t.vendors || []).join(" / ")}</li>`)
            .join("");
          return `<div class="mb-3"><div class="text-xs font-semibold text-slate-300 mb-1">${row.icon} ${row.analyst} <span class="text-slate-500 font-normal">(${row.pattern})</span></div><ul class="list-disc ml-4">${tl}${pf}</ul></div>`;
        }
        const tools = (row.tools || [])
          .map(
            (t) =>
              `<li class="text-xs text-slate-400"><code class="text-emerald-300">${t.tool}</code> → ${(t.vendors || []).join(" / ")} <span class="text-slate-600">(${t.tool_node})</span></li>`
          )
          .join("");
        return `<div class="mb-3"><div class="text-xs font-semibold text-slate-300 mb-1">${row.icon} ${row.analyst} <span class="text-slate-500 font-normal">(${row.pattern})</span></div><ul class="list-disc ml-4">${tools}</ul></div>`;
      })
      .join("");

    container.innerHTML = `
      <h3 class="text-sm font-semibold text-orange-300 mb-1">📡 Data sources</h3>
      <p class="text-[11px] text-slate-500 mb-3">External feeds and how they reach agents. Click a card to highlight on the graph.</p>
      <div class="grid grid-cols-1 md:grid-cols-2 gap-2 mb-4">${cards}</div>
      <h4 class="text-[10px] uppercase tracking-wider text-slate-500 mb-2">Tool routing by analyst</h4>
      <div class="rounded-lg border border-slate-800 bg-slate-950/30 p-3">${matrix || "<p class='text-xs text-slate-500'>No analysts selected.</p>"}</div>`;
  }

  function renderTimeline(container, timeline, activeField) {
    if (!container || !timeline) return;
    container.innerHTML = timeline
      .map((item, i) => {
        const active = activeField === item.field;
        const done = timeline.findIndex((t) => t.field === activeField) > i;
        const cls = active
          ? "border-emerald-400 bg-emerald-500/10 text-emerald-200"
          : done
            ? "border-slate-600 bg-slate-800/40 text-slate-400"
            : "border-slate-700 bg-slate-900/30 text-slate-500";
        return `<div class="shrink-0 px-3 py-2 rounded-lg border text-xs ${cls}" data-field="${item.field}">
          <div class="font-mono text-[10px]">${item.field}</div>
          <div class="text-[10px] opacity-70">${item.by}</div>
        </div>`;
      })
      .join("");
  }

  global.TAGraph = {
    createGraph,
    setNodeState,
    clearNodeStates,
    pulseToolActivity,
    markAgentProgress,
    renderDetailPanel,
    renderTimeline,
    renderMemorySection,
    renderDataSourcesSection,
    highlightNodes,
    setMemoryPhase,
    clearMemoryFlow,
    buildElements,
  };
})(window);
