"""Cross-agent handoff signalling.

When a voice agent says "let me bring in the Bear on this", we surface a
structured signal on the LiveKit room's data channel so the client can offer
the user a "Talk to Bear" handoff button. The worker emits the signal; this
module is the shared event schema both ends use.

Detecting handoff intent: the worker watches the assistant's outgoing TTS
text for any of the persona's ``handoff_targets`` (case-insensitive, word-
boundary match) preceded by a small set of intent phrases — "bring in",
"hand off to", "let's pull in", etc. A match emits exactly one HandoffSuggested
event per turn (de-duplicated on the target agent_id).

The client never trusts a *user* utterance to initiate a handoff — the user
just says "I want the Bear" and the same detector runs on the *assistant's*
acknowledgement, which gives the agent a chance to refuse if the user is
asking for someone not in the persona's allowed targets.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterable, List, Optional, Tuple

# Data-channel payload kinds. Keep these stable — three clients consume them.
KIND_HANDOFF_SUGGESTED = "handoff.suggested"
KIND_RECONCILE_REQUESTED = "reconcile.requested"
KIND_TRANSCRIPT_PARTIAL = "transcript.partial"
KIND_TRANSCRIPT_FINAL = "transcript.final"
KIND_USAGE = "usage"

# Panel-mode events: bracket each persona's spoken utterance so the in-call
# UI can pulse the active speaker's avatar in real time. ``turn_start`` is
# emitted just before the worker dispatches that persona's TTS chunk;
# ``turn_end`` lands when the audio has been synthesised (not necessarily
# fully played — clients should treat the gap as "still speaking").
KIND_PANEL_TURN_START = "panel.turn_start"
KIND_PANEL_TURN_END = "panel.turn_end"


@dataclass(frozen=True)
class HandoffSuggested:
    """The active agent suggested handing off to ``target_agent_id``."""

    source_agent_id: str
    target_agent_id: str
    quote: str       # The sentence that triggered detection (for UX)

    def to_dict(self) -> dict:
        return {
            "kind": KIND_HANDOFF_SUGGESTED,
            "source_agent_id": self.source_agent_id,
            "target_agent_id": self.target_agent_id,
            "quote": self.quote,
        }


@dataclass(frozen=True)
class ReconcileRequested:
    """User asked the Portfolio Manager to revisit the verdict.

    Emitted only from the Portfolio Manager session (see :mod:`reconcile`).
    Carries the user's objection so the client can render a "PM is revisiting"
    banner and the server can spin a one-shot reasoning pass.
    """

    run_id: str
    objection: str

    def to_dict(self) -> dict:
        return {
            "kind": KIND_RECONCILE_REQUESTED,
            "run_id": self.run_id,
            "objection": self.objection,
        }


# Intent phrases that, when followed within a few words by an agent name,
# count as a handoff suggestion. We keep this list small and deterministic so
# the agent prompt's wording doesn't drift away from what the detector sees.
_INTENT_PATTERNS = [
    r"bring(?:\s+in|ing\s+in)",
    r"hand(?:\s+off|ing\s+off)\s+to",
    r"pull(?:\s+in|ing\s+in)",
    r"loop(?:\s+in|ing\s+in)",
    r"defer(?:\s+to|ring\s+to)",
    r"ask\s+the",
    r"hear\s+from\s+the",
    r"check\s+with\s+the",
]

_INTENT_RE = re.compile(
    r"\b(?:" + "|".join(_INTENT_PATTERNS) + r")\b",
    flags=re.IGNORECASE,
)

# How many characters after the intent phrase we'll scan for a target name.
_TARGET_SEARCH_WINDOW = 60


def detect_handoff(
    utterance: str,
    *,
    source_agent_id: str,
    allowed_targets: Iterable[str],
    already_in_session: Iterable[str] = (),
) -> Optional[HandoffSuggested]:
    """Scan an assistant utterance for a handoff intent.

    Returns at most one :class:`HandoffSuggested`. We pick the *first* allowed
    target named within the search window after an intent phrase. If no intent
    phrase matches, returns ``None``.

    ``already_in_session`` lets a panel session suppress the suggestion when
    the target persona is already on the call (e.g. on a Bull/Bear panel,
    "let me bring in the Bear" is a no-op because the Bear is right there).
    """
    if not utterance:
        return None
    targets = list(allowed_targets)
    if not targets:
        return None
    in_session = {a for a in already_in_session if a}
    for intent in _INTENT_RE.finditer(utterance):
        window_start = intent.end()
        window = utterance[window_start : window_start + _TARGET_SEARCH_WINDOW]
        # Sort targets by length desc so "Bull Researcher" wins over "Bull".
        for target in sorted(targets, key=len, reverse=True):
            pat = re.compile(r"\b" + re.escape(target) + r"\b", flags=re.IGNORECASE)
            m = pat.search(window)
            if m:
                if target in in_session:
                    # Caller is already on the panel — surface no chip.
                    return None
                quote = _extract_quote(utterance, intent.start(), window_start + m.end())
                return HandoffSuggested(
                    source_agent_id=source_agent_id,
                    target_agent_id=target,
                    quote=quote,
                )
    return None


def _extract_quote(text: str, start: int, end: int) -> str:
    """Pull the surrounding sentence containing the matched range."""
    sentence_start = text.rfind(".", 0, start) + 1
    if sentence_start <= 0:
        sentence_start = 0
    sentence_end = text.find(".", end)
    if sentence_end < 0:
        sentence_end = min(len(text), end + 40)
    return text[sentence_start : sentence_end + 1].strip()


# ── Reconcile detection (Portfolio Manager only) ────────────────────────────

_RECONCILE_PATTERNS = [
    r"reconcile",
    r"revisit\s+(?:the\s+)?verdict",
    r"reconsider",
    r"change\s+your\s+(?:mind|rating|verdict|call)",
    r"updat(?:e|ing)\s+the\s+(?:rating|verdict|call)",
]

_RECONCILE_RE = re.compile(
    r"\b(?:" + "|".join(_RECONCILE_PATTERNS) + r")\b",
    flags=re.IGNORECASE,
)


def detect_reconcile_request(utterance: str) -> bool:
    """Whether a USER utterance asks the PM to revisit the verdict.

    The Portfolio Manager session wires this onto its user-side transcript;
    the resulting ``ReconcileRequested`` event triggers a re-invocation of
    ``portfolio_manager.py`` with the user's objection folded into context.
    """
    if not utterance:
        return False
    return bool(_RECONCILE_RE.search(utterance))


def coalesce_handoffs(
    suggestions: List[HandoffSuggested],
) -> List[HandoffSuggested]:
    """Deduplicate a list of suggestions, keeping the first per target.

    Calls can produce repeated "bring in the Bear" lines as the agent works
    through pushback; the client only needs the first one to surface the chip.
    """
    seen: Tuple[str, ...] = ()
    out: List[HandoffSuggested] = []
    for s in suggestions:
        if s.target_agent_id in seen:
            continue
        out.append(s)
        seen = seen + (s.target_agent_id,)
    return out
