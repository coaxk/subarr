"""#131 — plain-language explanation of a sweep result, via the in-stack local
ollama.

ollama's role here is EXPLANATION, not scoring (scoring is the validated LaBSE
QE judge — see QE_SCOPE). Static templated guidance can't reason about a
specific result; a local LLM can turn "all four scored 43.6, agreement 100%"
into "this clip is easy enough that settings don't matter — try a harder one".

Best-effort: returns None if ollama isn't configured or errors, so the
structured guidance always stands on its own.
"""
from __future__ import annotations

import logging

log = logging.getLogger(__name__)

_SYSTEM = (
    "You explain subtitle-tuning results to a self-hoster in plain, friendly "
    "language. 2-4 sentences, concrete and practical. No jargon, no markdown, "
    "no preamble. Only use the numbers given — never invent any."
)


def build_prompt(result: dict, media_path: str) -> str:
    rows = result.get("aggregate", []) or []
    per_clip = result.get("per_clip", []) or []
    nclips = len(per_clip)
    lines = [
        f"File sampled: {(media_path or '').split('/')[-1]}",
        f"Clips judged: {', '.join(c.get('kind', '?') for c in per_clip)} "
        f"(each a ~30s sample, judged on its own).",
        "Recipes by mean score (0-100, higher is better):",
    ]
    for r in rows:
        extra = f", unusable on {r['disqualified_in']} clip(s)" if r.get("disqualified_in") else ""
        lines.append(f"  - {r['label']}: {r['mean_composite']} (won {r.get('clips_won', 0)}/{nclips}{extra})")
    if result.get("tie"):
        lines.append("Outcome: effectively a TIE — the top recipes scored within noise of each other.")
    elif result.get("winner"):
        lines.append(f"Outcome: top pick is '{result['winner']}', confidence {result.get('confidence')}.")
    else:
        lines.append("Outcome: nothing produced usable output.")
    if result.get("agreement_mean") is not None:
        lines.append(f"The recipes agreed {round(result['agreement_mean'] * 100)}% on what was said.")
    lines.append(
        "Explain what this means and what to do next. If it's a tie, say the audio is easy enough that "
        "settings don't matter here, and suggest trying a harder clip (foreign-language, noisy, or with a "
        "music/quiet stretch) to actually separate the recipes. If there's a clear winner, say they can "
        "adopt its settings as their default."
    )
    return "\n".join(lines)


async def explain(result: dict, media_path: str, ollama) -> str | None:
    if ollama is None or not getattr(ollama, "is_configured", lambda: False)():
        return None
    try:
        # num_predict default (64) truncates mid-sentence; 2-4 sentences need ~220.
        text = await ollama.generate(build_prompt(result, media_path), system=_SYSTEM, num_predict=220)
        return (text or "").strip() or None
    except Exception as e:  # pragma: no cover - best effort
        log.debug("arena explain failed: %s", e)
        return None
