#!/usr/bin/env python
"""#616 prompt eval: is naming the library and relative path better than the
old title + folder-path prompt?

Each arm asks the LLM the same questions and is scored against the arrs' own
`originalLanguage`, the closest thing to ground truth we have. Read-only: it
never writes to the enrichment store or to settings.

Usage (piped in, so no bind-mount needed):
    cat scripts/eval_title_language.py | docker exec -i subarr-next python - --n 60 --arm both
"""

from __future__ import annotations

import argparse
import asyncio
import random

OLD_SYSTEM_PROMPT = (
    "You are a media-cataloguing assistant. Given a TV series or movie title "
    "and folder path, identify the ORIGINAL spoken language of the production. "
    "Output a JSON object with: iso_code (ISO 639-1 two-letter code, or 'und' "
    "if you can't tell), confidence (0.0 to 1.0), and a one-sentence reasoning. "
    "Examples of ISO codes: en, fr, es, de, it, ja, ko, zh, ru, pl, pt, hi."
)
OLD_USER_TEMPLATE = "Title: {title}\nFolder path: {path}"


def canonical_for(arr_path: str, libraries) -> str | None:
    """Canonical for an arr path, or None when no library owns it."""
    for lib in sorted(libraries, key=lambda lib: len(lib.arr_prefix), reverse=True):
        prefix = lib.arr_prefix.rstrip("/") if lib.arr_prefix else ""
        if not prefix:
            continue
        if arr_path.rstrip("/") != prefix and not arr_path.startswith(prefix + "/"):
            continue
        rest = arr_path[len(prefix) :].strip("/")
        return f"@{lib.slug}/{rest}" if lib.slug else rest
    return None


def truth_code(item: dict) -> str | None:
    """The item's originalLanguage as an ISO code subarr knows, or None."""
    from subarr.langs import WHISPER_LANGUAGES, normalize_lang

    code = normalize_lang((item.get("originalLanguage") or {}).get("name"))
    return code if code in WHISPER_LANGUAGES else None


def pick_sample(rows: list[dict], n: int, seed: int) -> list[dict]:
    """A seeded half-English/half-other sample, English rows first."""
    rng = random.Random(seed)
    english = sorted((r for r in rows if r["truth"] == "en"), key=lambda r: (r["title"], r["canonical"]))
    others = sorted((r for r in rows if r["truth"] != "en"), key=lambda r: (r["title"], r["canonical"]))
    rng.shuffle(english)
    rng.shuffle(others)
    return english[: n // 2] + others[: n - n // 2]


async def ask(client, system: str, prompt: str) -> tuple[str | None, str | None]:
    """One LLM call: (iso, None) or (None, error). Cooling down propagates."""
    from subarr.enrichment import _LANG_SCHEMA, _parse_structured
    from subarr.integrations.llm import LLMError
    from subarr.integrations.openai_compat import LLMCoolingDown

    try:
        raw = await client.generate(
            prompt,
            system=system,
            temperature=0.0,
            num_predict=120,
            format_schema=_LANG_SCHEMA,
            schema_name="subarr_lang",
        )
        iso, _, _ = _parse_structured(raw)
        return iso, None
    except LLMCoolingDown:
        raise
    except LLMError as e:
        return None, str(e)


def score(results: list[dict]) -> dict:
    """Correct counts overall/English/other plus the rows that disagree."""
    english = [r for r in results if r["truth"] == "en"]
    others = [r for r in results if r["truth"] != "en"]
    return {
        "correct": sum(1 for r in results if r["guess"] == r["truth"]),
        "n": len(results),
        "en_correct": sum(1 for r in english if r["guess"] == r["truth"]),
        "en_n": len(english),
        "other_correct": sum(1 for r in others if r["guess"] == r["truth"]),
        "other_n": len(others),
        "disagreements": [r for r in results if r["guess"] != r["truth"]],
    }


async def _evaluate(args) -> int:
    from subarr import config
    from subarr.enrichment import _SYSTEM_PROMPT, build_user_prompt
    from subarr.integrations.llm import build_llm_client
    from subarr.integrations.radarr import RadarrClient
    from subarr.integrations.sonarr import SonarrClient

    llm = build_llm_client()
    if not llm.is_configured():
        print("LLM not configured")
        return 1
    sonarr = SonarrClient()
    radarr = RadarrClient()
    try:
        rows: list[dict] = []
        for name, arr, fetch in (("sonarr", sonarr, sonarr.series), ("radarr", radarr, radarr.movies)):
            try:
                items = await fetch()
            except Exception as e:  # noqa: BLE001
                print(f"{name}: skipped ({type(e).__name__})")
                continue
            for item in items:
                path = item.get("path")
                if not isinstance(path, str) or not path:
                    continue
                truth = truth_code(item)
                canonical = canonical_for(path, config.settings.libraries)
                if truth is None or canonical is None:
                    continue
                rows.append({"title": str(item.get("title") or ""), "canonical": canonical, "truth": truth})

        sample = pick_sample(rows, args.n, args.seed)
        print(f"sample: {len(sample)} of {args.n} requested")
        if not sample:
            print("no rows to evaluate")
            return 1

        arms = ("old", "new") if args.arm == "both" else (args.arm,)
        for arm in arms:
            results: list[dict] = []
            for row in sample:
                if arm == "old":
                    system = OLD_SYSTEM_PROMPT
                    prompt = OLD_USER_TEMPLATE.format(title=row["title"], path=row["canonical"])
                else:
                    system = _SYSTEM_PROMPT
                    prompt = build_user_prompt(row["title"], row["canonical"])
                guess, error = await ask(llm, system, prompt)
                results.append({**row, "guess": guess, "error": error})
            stats = score(results)
            print(
                f"{arm}: {stats['correct']}/{stats['n']} correct "
                f"(en {stats['en_correct']}/{stats['en_n']}, "
                f"other {stats['other_correct']}/{stats['other_n']})"
            )
            for r in stats["disagreements"]:
                line = f"  {r['canonical']} truth={r['truth']} guess={r['guess'] or '-'}"
                if r["error"]:
                    line += f" error={r['error']}"
                print(line)
        return 0
    finally:
        for closer in (llm, sonarr, radarr):
            try:
                await closer.aclose()
            except Exception:  # noqa: BLE001, S110
                pass


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Compare the old and new enrichment prompts.")
    parser.add_argument("--n", type=int, default=60, help="rows to sample (at least 2)")
    parser.add_argument("--arm", choices=("old", "new", "both"), default="both")
    parser.add_argument("--seed", type=int, default=0)
    args = parser.parse_args(argv)
    if args.n < 2:
        parser.error("--n must be >= 2")
    return asyncio.run(_evaluate(args))


if __name__ == "__main__":
    raise SystemExit(main())
