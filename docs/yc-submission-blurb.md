# YC Application — "Coding agent session" field

Three drop-in versions of the submission text, depending on how much room
the YC form gives you. Paste whichever fits, and attach
[`docs/yc-coding-session-voice-agents.md`](./yc-coding-session-voice-agents.md)
(or a Cursor share link to the same session — see "How to share" below).

---

## Version A — one-liner (≤ 280 chars, for tight fields)

> In one 75-minute Cursor session I turned a one-paragraph product vision
> ("users should be able to voice-call any of our 12 agents") into a
> shipped, feature-flagged voice subsystem — Python backend, FastAPI router,
> Supabase migrations, SwiftUI + React Native clients, and tests.

---

## Version B — short paragraph (~600 chars, recommended)

> This is the Cursor session where the differentiating feature of our app
> went from vision to shipped code. I gave the agent one paragraph — "I
> want users to be able to voice-call any of our 12 trading agents and
> push back on their reasoning" — and in ~75 minutes (6 user prompts, 125
> agent steps, 122 tool calls) it scoped the stack, designed a 12-persona
> system, built the Python voice subsystem, FastAPI router, Supabase
> migrations, SwiftUI + React Native voice clients, an A/B framework, a
> cost dashboard, and tests. The whole thing landed behind a feature flag
> so the existing web and iOS apps were untouched.

---

## Version C — full context (~1.2k chars, for the long-answer field)

> The session linked here is the one where the **differentiating feature**
> of our YC pitch went from vision to shipped code. StockAgents already
> runs a 12-agent LLM "trading desk" on any stock — analysts, bull/bear
> researchers, a trader, a risk panel, and a portfolio manager who issues
> a verdict. The wedge that makes us not-another-stock-chatbot is that the
> user can then **voice-call any one of those agents** and challenge their
> reasoning live.
>
> I started the session with one paragraph: *"All 12 agents should be
> voice-driven so users can have real-time conversations with them and
> push back."* The agent asked three clarifying questions (conversation
> model, voice stack, platform scope), I picked the answers, and over the
> next ~75 minutes it shipped: a 12-persona system with branded voices and
> style prompts (`tradingagents/voice/personas.py`), a LiveKit + Deepgram +
> ElevenLabs agent worker, a FastAPI router with mobile-auth gating, two
> Supabase migrations, SwiftUI + React Native voice clients with parity UI,
> a deterministic A/B variant framework, a cost dashboard, and two test
> suites — all behind `TRADINGAGENTS_VOICE_ENABLED` so the existing web,
> iOS, and RN clients were completely untouched. 454 tests passing,
> TypeScript strict-clean, linters green.
>
> Stats: 6 user prompts, 125 agent steps, 122 tool calls (53× edits, 34×
> reads, 13× shell, 8× new files, 1× spawned a subagent for codebase
> mapping). Verbatim transcript attached.

---

## How to share

You have **two equally good options** — pick whichever the YC form prefers:

### Option 1 — Cursor public share link (preferred)

1. In Cursor, open the chat sidebar and find the chat titled something
   like *"Voice agents vision"* from **Saturday, Jun 13, 2026, 9:15 PM**.
2. Click the `⋯` menu at the top of that chat → **Share** → **Create
   public link**.
3. Paste the resulting `https://cursor.com/share/...` URL into the YC
   field. (A YC partner can scrub through the real Cursor UI — tool
   calls expand inline, file diffs render, etc.)

### Option 2 — File upload (good fallback)

Upload [`docs/yc-coding-session-voice-agents.md`](./yc-coding-session-voice-agents.md)
directly. It's a clean 633-line verbatim export with tool calls collapsed
into one-line summaries. Reviewer can skim it in ~2 minutes.

### Option 3 — Add a 60-second Loom (highest impact, optional)

Record yourself scrolling through the session in Cursor while narrating:

> *"Here's where I gave the agent the voice-agents vision in one paragraph.
> It asked three clarifying questions, then in this run it scoped the stack,
> built the persona system, the LiveKit worker, the FastAPI router, two
> mobile clients, and the test suite. Total: ~75 minutes, 454 tests still
> passing, the existing app untouched behind a feature flag. Here's a 5-min
> voice call with the Bull Researcher running on it right now."*

If you do all three (Loom + share link + file), put the Loom URL in the
main field and the share link + file in the optional context.

---

## Session metadata (for reference)

| | |
|---|---|
| **Session ID** | `484a449c-1213-4331-866b-b927ce11d309` |
| **Date** | Sat Jun 13, 2026, 21:15 → 22:31 PT |
| **Duration** | ~76 minutes |
| **User prompts** | 6 |
| **Agent steps** | 125 |
| **Tool calls** | 122 (53× StrReplace, 34× Read, 13× Shell, 8× Write, 4× TodoWrite, 3× WebSearch, 2× ReadLints, 1× Task) |
| **Files created** | 19 new files across backend, infra, iOS, RN, tests |
| **Files modified** | 6 (additive — feature flag gated) |
| **Tests added** | 13 (`test_voice_personas.py` + `test_voice_handoff.py`) |
| **Final test suite** | 454 passed, 1 skipped, all linters clean |
