/* TradingAgents — voice-conversational web client.
 *
 * Pulls in LiveKit's vanilla `livekit-client` SDK from a CDN (no bundler in
 * this app), exposes a tiny `window.VoiceClient` surface, and renders a
 * floating in-call panel with live transcript, handoff chips, and an "ask
 * the PM to reconcile" button when talking to the Portfolio Manager.
 *
 * Lifecycle:
 *   const session = await VoiceClient.start({ runId, agentId });
 *   // user clicks hang-up
 *   await session.hangup();
 *
 * Everything in this file is additive — index.html drives it by adding a
 * "Talk to <agent>" button to each completed agent card. No existing
 * agent-feed code is modified beyond wiring the button.
 */

(function () {
  "use strict";

  // ── LiveKit SDK loader ────────────────────────────────────────────────
  // The SDK is large enough that we don't want to ship it on first paint;
  // load it lazily on the first Talk click. `loadLiveKit` is idempotent.
  const LIVEKIT_CDN =
    "https://cdn.jsdelivr.net/npm/livekit-client@2.5.7/dist/livekit-client.umd.min.js";
  let _liveKitPromise = null;
  function loadLiveKit() {
    if (window.LivekitClient) return Promise.resolve(window.LivekitClient);
    if (_liveKitPromise) return _liveKitPromise;
    _liveKitPromise = new Promise((resolve, reject) => {
      const s = document.createElement("script");
      s.src = LIVEKIT_CDN;
      s.async = true;
      s.onload = () => {
        if (window.LivekitClient) resolve(window.LivekitClient);
        else reject(new Error("livekit-client loaded but window.LivekitClient missing"));
      };
      s.onerror = () => reject(new Error("failed to load livekit-client"));
      document.head.appendChild(s);
    });
    return _liveKitPromise;
  }

  // ── Voice config probe (cached) ───────────────────────────────────────
  let _config = null;
  let _configPromise = null;
  function fetchConfig() {
    if (_config) return Promise.resolve(_config);
    if (_configPromise) return _configPromise;
    _configPromise = fetch("/api/voice/config", { credentials: "same-origin" })
      .then((r) => (r.ok ? r.json() : { enabled: false, ready: false }))
      .then((c) => {
        _config = c;
        _configPromise = null;
        return c;
      })
      .catch(() => {
        _config = { enabled: false, ready: false, reason: "config probe failed" };
        _configPromise = null;
        return _config;
      });
    return _configPromise;
  }

  /** True iff the backend reported voice is enabled AND fully configured. */
  async function isReady() {
    const cfg = await fetchConfig();
    return Boolean(cfg.ready);
  }

  // ── Auth header ───────────────────────────────────────────────────────
  // Voice endpoints accept the same bearer the mobile API uses. The web
  // app today doesn't sign in to the mobile API, so we just send what we
  // have (visitor id) and the router accepts anonymous web visitors.
  function authHeaders() {
    const headers = { "Content-Type": "application/json" };
    if (window.Analytics && typeof window.Analytics.headers === "function") {
      Object.assign(headers, window.Analytics.headers());
    }
    return headers;
  }

  // ── Backend calls ─────────────────────────────────────────────────────
  async function createSession({ runId, agentId }) {
    const res = await fetch("/api/voice/sessions", {
      method: "POST",
      headers: authHeaders(),
      body: JSON.stringify({ run_id: runId, agent_id: agentId }),
      credentials: "same-origin",
    });
    if (!res.ok) {
      const detail = await res.json().catch(() => null);
      const err = new Error(
        (detail && (detail.detail?.message || detail.detail)) ||
          `voice session failed (${res.status})`
      );
      err.status = res.status;
      err.detail = detail;
      throw err;
    }
    return res.json();
  }

  async function postReconcile(sessionId, objection) {
    const res = await fetch(
      `/api/voice/sessions/${encodeURIComponent(sessionId)}/reconcile`,
      {
        method: "POST",
        headers: authHeaders(),
        body: JSON.stringify({ objection }),
        credentials: "same-origin",
      }
    );
    if (!res.ok) {
      const detail = await res.json().catch(() => null);
      const err = new Error(
        (detail && (detail.detail?.message || detail.detail)) ||
          `reconcile failed (${res.status})`
      );
      err.status = res.status;
      throw err;
    }
    return res.json();
  }

  // ── UI: floating in-call panel ────────────────────────────────────────
  // Built lazily on first start(). Reused across hangup → reopen so we keep
  // a single transcript history per page session.
  let panelEl = null;

  // Teardown fn for the currently-live call, if any. Used to guarantee a call
  // is fully hung up (room disconnected + mic stopped) before another starts,
  // so a re-entrant start() can never leave two live rooms / two hot mics.
  let _teardownActive = null;

  function buildPanel() {
    if (panelEl) return panelEl;
    panelEl = document.createElement("div");
    panelEl.id = "voice-panel";
    panelEl.className =
      "fixed bottom-4 right-4 z-50 w-[360px] max-w-[92vw] hidden " +
      "bg-slate-950/95 backdrop-blur border border-slate-700 rounded-2xl " +
      "shadow-2xl shadow-black/60 text-slate-200 overflow-hidden";
    panelEl.innerHTML = `
      <div data-head class="flex items-center gap-3 px-4 py-3 border-b border-slate-800">
        <div data-avatar class="w-10 h-10 rounded-full flex items-center justify-center text-lg"
             style="background:rgba(52,211,153,.15);border:1px solid rgba(52,211,153,.5)">🎙</div>
        <div class="flex-1 min-w-0">
          <div data-name class="font-semibold text-slate-100 truncate">—</div>
          <div class="flex items-center gap-2">
            <div data-status class="text-[11px] text-slate-400 truncate">connecting…</div>
            <div data-latency class="text-[10px] text-slate-500 truncate font-mono"></div>
          </div>
        </div>
        <button data-mute title="Mute mic"
          class="w-8 h-8 rounded-full border border-slate-700 hover:border-slate-500
                 flex items-center justify-center text-slate-300">🎙</button>
        <button data-hang title="Hang up"
          class="w-8 h-8 rounded-full bg-rose-500/20 border border-rose-500/50
                 hover:bg-rose-500/30 flex items-center justify-center text-rose-200">✕</button>
      </div>
      <div data-transcript class="px-4 py-3 space-y-2 max-h-[40vh] overflow-y-auto text-sm"></div>
      <div data-chips class="px-4 pb-2 flex flex-wrap gap-1.5"></div>
      <div data-reconcile class="hidden border-t border-slate-800 px-4 py-3 space-y-2">
        <div class="text-[11px] uppercase tracking-wide text-slate-500">Ask the PM to reconcile</div>
        <textarea data-recon-text rows="2"
          placeholder="What's your objection? e.g. 'You're under-weighting the new product cycle.'"
          class="w-full bg-slate-900 border border-slate-700 rounded-lg px-2.5 py-1.5 text-xs
                 focus:outline-none focus:border-emerald-400"></textarea>
        <div class="flex justify-end">
          <button data-recon-submit
            class="text-xs px-3 py-1.5 rounded-lg bg-emerald-500/20 border border-emerald-500/50
                   text-emerald-200 hover:bg-emerald-500/30">Reconcile</button>
        </div>
        <div data-recon-out class="hidden text-xs text-slate-300 leading-relaxed border-l-2 border-emerald-400 pl-3"></div>
      </div>
    `;
    document.body.appendChild(panelEl);
    return panelEl;
  }

  // ── Latency telemetry (Phase 6 latency tracing) ─────────────────────
  // The worker publishes a ``usage`` event after each assistant turn with
  // the wall-clock ms from user-ASR-final to assistant-final. We keep a
  // simple rolling buffer and surface the latest sample in the status
  // line so the operator can spot regressions during testing.
  const _latencyBuffer = [];
  function recordLatency(ms) {
    _latencyBuffer.push(ms);
    if (_latencyBuffer.length > 32) _latencyBuffer.shift();
    if (window.console && typeof window.console.info === "function") {
      window.console.info("[voice] turn latency ms:", ms);
    }
    const u = ui();
    if (!u.latency) return;
    const last = ms;
    const p95 = percentile(_latencyBuffer, 95);
    u.latency.textContent = `Δ ${last}ms · p95 ${p95}ms`;
  }
  function percentile(arr, p) {
    if (!arr.length) return 0;
    const sorted = arr.slice().sort((a, b) => a - b);
    const idx = Math.min(sorted.length - 1, Math.floor((p / 100) * sorted.length));
    return sorted[idx];
  }

  function ui() {
    const p = buildPanel();
    return {
      panel: p,
      head: p.querySelector("[data-head]"),
      avatar: p.querySelector("[data-avatar]"),
      name: p.querySelector("[data-name]"),
      status: p.querySelector("[data-status]"),
      latency: p.querySelector("[data-latency]"),
      mute: p.querySelector("[data-mute]"),
      hang: p.querySelector("[data-hang]"),
      transcript: p.querySelector("[data-transcript]"),
      chips: p.querySelector("[data-chips]"),
      reconcile: p.querySelector("[data-reconcile]"),
      reconText: p.querySelector("[data-recon-text]"),
      reconSubmit: p.querySelector("[data-recon-submit]"),
      reconOut: p.querySelector("[data-recon-out]"),
    };
  }

  function setStatus(text, color) {
    const u = ui();
    u.status.textContent = text;
    u.status.style.color = color || "";
  }

  function appendTurn(role, text, partialKey) {
    const u = ui();
    let row;
    if (partialKey) {
      row = u.transcript.querySelector(`[data-key="${partialKey}"]`);
      if (!row) {
        row = document.createElement("div");
        row.setAttribute("data-key", partialKey);
        row.className = "flex gap-2";
        row.innerHTML = `<span class="text-[10px] uppercase tracking-wide pt-0.5"></span>
                         <span class="flex-1 text-slate-300"></span>`;
        u.transcript.appendChild(row);
      }
    } else {
      row = document.createElement("div");
      row.className = "flex gap-2";
      row.innerHTML = `<span class="text-[10px] uppercase tracking-wide pt-0.5"></span>
                       <span class="flex-1"></span>`;
      u.transcript.appendChild(row);
    }
    const tag = row.firstElementChild;
    const body = row.lastElementChild;
    const roleColor =
      role === "user" ? "#94a3b8" : role === "reconcile" ? "#34d399" : "#a7f3d0";
    tag.style.color = roleColor;
    tag.textContent = role === "user" ? "you" : role === "reconcile" ? "pm·new" : "agent";
    body.textContent = text;
    body.style.color = role === "user" ? "#cbd5e1" : "#f1f5f9";
    if (role === "reconcile") body.style.color = "#a7f3d0";
    u.transcript.scrollTop = u.transcript.scrollHeight;
  }

  function clearTranscript() {
    ui().transcript.innerHTML = "";
    ui().chips.innerHTML = "";
    const u = ui();
    u.reconcile.classList.add("hidden");
    u.reconOut.classList.add("hidden");
    u.reconOut.textContent = "";
  }

  function showHandoffChip(targetId, onPick) {
    const u = ui();
    if (u.chips.querySelector(`[data-target="${targetId}"]`)) return;
    const chip = document.createElement("button");
    chip.setAttribute("data-target", targetId);
    chip.className =
      "text-[11px] px-2.5 py-1 rounded-full bg-emerald-500/15 border " +
      "border-emerald-500/45 text-emerald-200 hover:bg-emerald-500/30";
    chip.textContent = `Talk to ${targetId} →`;
    chip.onclick = () => onPick(targetId);
    u.chips.appendChild(chip);
  }

  // ── Session controller ────────────────────────────────────────────────
  /**
   * Start a voice call with one agent. Returns a thin controller exposing
   * `hangup()` and a few status accessors. The on-screen panel takes care
   * of mute/hangup and is shared across calls (consecutive calls reuse it).
   */
  async function start({ runId, agentId }) {
    if (!runId || !agentId) throw new Error("runId and agentId are required");
    const cfg = await fetchConfig();
    if (!cfg.ready) throw new Error(cfg.reason || "voice is not configured");

    // Guard against a re-entrant start (rapid Talk clicks / a handoff racing an
    // in-flight start): fully tear down any existing call before opening a new
    // one so we never end up with two live rooms holding the mic.
    if (_teardownActive) {
      try { await _teardownActive(); } catch { /* ignore */ }
      _teardownActive = null;
    }

    const u = ui();
    u.panel.classList.remove("hidden");
    clearTranscript();
    setStatus("Connecting…", "#fbbf24");
    u.name.textContent = `${agentId}`;
    u.avatar.textContent = "🎙";

    // Wire the ✕ button BEFORE any awaits. Until the room exists, "cancel"
    // just means: hide the panel and set a flag the post-await steps
    // check, so a user can bail out of a slow/stuck "Connecting…". The
    // full hangup() (defined below) replaces this once the room is open.
    let cancelled = false;
    u.hang.onclick = () => {
      cancelled = true;
      u.panel.classList.add("hidden");
      setStatus("Cancelled", "#94a3b8");
    };

    // 1) Ask backend for room JWT + persona payload.
    let sess;
    try {
      sess = await createSession({ runId, agentId });
    } catch (err) {
      u.panel.classList.add("hidden");
      throw err;
    }
    if (cancelled) return null;
    u.name.textContent = `${sess.persona.display_name}`;
    if (sess.persona.voice_name) {
      u.head.title = `Voice: ${sess.persona.voice_name}`;
    }

    // 2) Lazy-load the LiveKit SDK.
    const LK = await loadLiveKit();
    const room = new LK.Room({
      adaptiveStream: true,
      dynacast: true,
    });

    // 3) Wire room events.
    let alive = true;
    const cleanups = [];

    function track(eventName, handler) {
      room.on(LK.RoomEvent[eventName] || eventName, handler);
      cleanups.push(() => room.off(LK.RoomEvent[eventName] || eventName, handler));
    }

    track("Disconnected", () => {
      alive = false;
      setStatus("Call ended", "#94a3b8");
    });
    track("Reconnecting", () => setStatus("Reconnecting…", "#fbbf24"));
    track("Reconnected", () => setStatus("Connected", "#34d399"));
    track("Connected", () => setStatus("Connected — speak any time", "#34d399"));

    // The agent publishes audio; we just need to play it.
    track("TrackSubscribed", (track, _pub, participant) => {
      if (track.kind === LK.Track.Kind.Audio) {
        const audioEl = track.attach();
        audioEl.autoplay = true;
        audioEl.style.display = "none";
        document.body.appendChild(audioEl);
        cleanups.push(() => {
          track.detach(audioEl);
          audioEl.remove();
        });
      }
    });

    // Data channel — handoff chips, transcript echo, reconcile signals.
    track("DataReceived", (payload, _participant, _kind) => {
      let msg;
      try {
        msg = JSON.parse(new TextDecoder().decode(payload));
      } catch {
        return;
      }
      if (!msg || !msg.kind) return;
      switch (msg.kind) {
        case "handoff.suggested":
          if (msg.target_agent_id) {
            showHandoffChip(msg.target_agent_id, async (target) => {
              // Hang up the current call, then start a fresh one. We can't
              // attach a second agent to the same room in v1 (server design
              // is one-agent-per-room); the handoff is a "new call to a
              // different persona on the same run".
              try {
                await hangup();
              } catch (e) {
                /* ignore */
              }
              start({ runId, agentId: target }).catch((err) => {
                setStatus(err.message || "handoff failed", "#f43f5e");
              });
            });
          }
          break;
        case "transcript.partial":
          appendTurn(msg.role || "assistant", msg.text || "", "partial-" + (msg.role || "x"));
          break;
        case "transcript.final": {
          // Remove the partial row if present, then push a final one.
          const u2 = ui();
          const partialKey = "partial-" + (msg.role || "x");
          const partialRow = u2.transcript.querySelector(`[data-key="${partialKey}"]`);
          if (partialRow) partialRow.remove();
          appendTurn(msg.role || "assistant", msg.text || "");
          break;
        }
        case "reconcile.requested":
          // The worker noticed the user asked to reconcile; reveal the
          // explicit reconcile input so they can refine and submit.
          ui().reconcile.classList.remove("hidden");
          ui().reconText.value = msg.objection || "";
          ui().reconText.focus();
          break;
        case "usage":
          // Per-turn latency telemetry emitted by the worker. We surface
          // the rolling worst-case in the status bar (without disrupting
          // the existing status text) so the operator can spot regressions
          // during user testing.
          if (typeof msg.rtt_ms === "number") {
            recordLatency(msg.rtt_ms);
          }
          break;
        default:
          // Unknown payload kind — ignore (forward-compat).
          break;
      }
    });

    // 4) Connect with the JWT and enable the mic.
    try {
      await room.connect(sess.url, sess.token, { autoSubscribe: true });
    } catch (err) {
      u.panel.classList.add("hidden");
      throw err;
    }
    if (cancelled) {
      try { await room.disconnect(true); } catch { /* ignore */ }
      return null;
    }
    await room.localParticipant.setMicrophoneEnabled(true);
    if (cancelled) {
      try { await room.disconnect(true); } catch { /* ignore */ }
      return null;
    }

    // 5) Wire UI controls.
    let muted = false;
    u.mute.onclick = async () => {
      muted = !muted;
      try {
        await room.localParticipant.setMicrophoneEnabled(!muted);
        u.mute.style.background = muted ? "rgba(244,63,94,.2)" : "";
        u.mute.style.borderColor = muted ? "rgba(244,63,94,.5)" : "";
        u.mute.title = muted ? "Unmute mic" : "Mute mic";
      } catch (err) {
        setStatus("Mic toggle failed", "#f43f5e");
      }
    };

    async function hangup() {
      if (!alive) {
        // Already torn down — just make sure the panel is hidden so the
        // user isn't stuck staring at a stale "Call ended" overlay.
        if (_teardownActive === hangup) _teardownActive = null;
        u.panel.classList.add("hidden");
        return;
      }
      alive = false;
      // Tear down every step independently and defensively: a throw from any one
      // step (e.g. a rejected disconnect) must NOT skip the others, or the old
      // room stays connected with the mic hot. Explicitly stop the local mic
      // regardless of whether room.disconnect() succeeds.
      try {
        await room.localParticipant.setMicrophoneEnabled(false);
      } catch { /* ignore */ }
      try {
        room.localParticipant.trackPublications.forEach((pub) => {
          try { if (pub.track) pub.track.stop(); } catch { /* ignore */ }
        });
      } catch { /* ignore */ }
      try {
        await room.disconnect(true);
      } catch { /* ignore */ }
      cleanups.forEach((fn) => {
        try {
          fn();
        } catch {
          /* ignore */
        }
      });
      if (_teardownActive === hangup) _teardownActive = null;
      setStatus("Call ended", "#94a3b8");
      // Hide the panel on user-initiated hangup. The panel itself is
      // reused across calls (singleton), so we only toggle visibility.
      u.panel.classList.add("hidden");
    }
    u.hang.onclick = hangup;
    // This call is now the live one; record its teardown for the re-entrancy
    // guard at the top of start().
    _teardownActive = hangup;

    // Reconcile button (Portfolio Manager only).
    if (agentId === "Portfolio Manager") {
      u.reconcile.classList.remove("hidden");
      u.reconSubmit.onclick = async () => {
        const txt = (u.reconText.value || "").trim();
        if (txt.length < 8) {
          u.reconOut.classList.remove("hidden");
          u.reconOut.style.color = "#fb7185";
          u.reconOut.textContent =
            "Add a few more words so I can give you a real answer.";
          return;
        }
        u.reconSubmit.disabled = true;
        u.reconOut.classList.remove("hidden");
        u.reconOut.style.color = "#94a3b8";
        u.reconOut.textContent = "Thinking…";
        try {
          const result = await postReconcile(sess.session_id, txt);
          u.reconOut.style.color = "";
          const flipped = result.flipped
            ? `<span style="color:#fbbf24"> (flipped to ${escHtml(result.updated_decision)})</span>`
            : "";
          u.reconOut.innerHTML =
            `<div class="text-[10px] uppercase tracking-wide text-emerald-400 mb-1">Updated rationale${flipped}</div>` +
            renderMarkdown(result.rationale_markdown || "");
          appendTurn("reconcile", result.rationale_markdown || "");
        } catch (err) {
          u.reconOut.style.color = "#f43f5e";
          u.reconOut.textContent = err.message || "Reconcile failed.";
        } finally {
          u.reconSubmit.disabled = false;
        }
      };
    } else {
      u.reconcile.classList.add("hidden");
    }

    if (window.Analytics) {
      window.Analytics.track("voice_session_started", {
        run_id: runId,
        agent_id: agentId,
        session_id: sess.session_id,
      });
    }

    return {
      sessionId: sess.session_id,
      hangup,
      isAlive: () => alive,
    };
  }

  // Escape a plain string for safe interpolation into innerHTML.
  function escHtml(s) {
    const d = document.createElement("div");
    d.textContent = s == null ? "" : String(s);
    return d.innerHTML;
  }

  function renderMarkdown(text) {
    // The reconcile rationale is LLM-authored, so its HTML must be sanitized
    // before it ever reaches innerHTML. DOMPurify is loaded by the host page
    // (index.html). If it isn't present we refuse to trust marked's HTML and
    // fall back to escaped plaintext rather than risk injecting a script.
    const sanitize = (html) =>
      window.DOMPurify && typeof window.DOMPurify.sanitize === "function"
        ? window.DOMPurify.sanitize(html)
        : null;
    if (window.marked && typeof window.marked.parse === "function") {
      try {
        const clean = sanitize(window.marked.parse(text || ""));
        if (clean != null) return clean;
      } catch {
        /* fall through */
      }
    }
    // Plaintext fallback — preserve newlines, escape HTML.
    const div = document.createElement("div");
    div.textContent = text || "";
    return div.innerHTML.replace(/\n/g, "<br>");
  }

  // ── Public surface ────────────────────────────────────────────────────
  window.VoiceClient = { start, isReady, fetchConfig };
})();
