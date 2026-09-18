"""`:latest` has exactly one owner: a release tag.

Until 2026-09-18 both builds of a release wrote `:latest`: the main push (an
explicit raw rule) and the tag push (docker/metadata-action's default
`latest=auto` on a semver tag). Whichever finished last owned the tag, so
v2.7.10's `:latest` was the release build and v2.7.11's was the main build,
labelled `version=main`. The README's default install is `:latest`, and
image-staleness.yml scans it as "the image users pull", so it must be the
newest release, deterministically.
"""

from __future__ import annotations

import re
from pathlib import Path

import yaml

WORKFLOW = Path(__file__).resolve().parents[1] / ".github" / "workflows" / "release.yml"


def _meta_step() -> dict:
    wf = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    for job in wf["jobs"].values():
        for step in job.get("steps", []):
            if "docker/metadata-action" in str(step.get("uses", "")):
                return step
    raise AssertionError("no docker/metadata-action step in release.yml")


def _latest_enable_expr(tags: str) -> str:
    rules = [ln.strip() for ln in tags.splitlines() if "latest" in ln]
    assert len(rules) == 1, f"exactly one rule may mention latest, found {rules}"
    m = re.fullmatch(r"type=raw,value=latest,enable=\$\{\{ (.+) \}\}", rules[0])
    assert m, rules[0]
    return m.group(1)


_CLAUSE = re.compile(r"(!?)(startsWith|contains)\(github\.ref, '([^']*)'\)")


def _evaluate(expr: str, ref: str) -> bool:
    """Evaluate the expression shape this rule uses: clauses of
    `[!]startsWith|contains(github.ref, '...')` joined by `&&`. Anything else
    fails loudly, so a rewrite of the rule has to update this test too."""
    result = True
    for clause in (c.strip() for c in expr.split("&&")):
        m = _CLAUSE.fullmatch(clause)
        assert m, f"unsupported clause in the latest rule: {clause!r}"
        neg, fn, arg = m.groups()
        hit = ref.startswith(arg) if fn == "startsWith" else arg in ref
        result = result and (not hit if neg else hit)
    return result


def test_implicit_latest_is_switched_off():
    flavor = str(_meta_step()["with"].get("flavor", ""))
    assert "latest=false" in flavor.replace(" ", "")


def test_only_a_release_tag_moves_latest():
    expr = _latest_enable_expr(_meta_step()["with"]["tags"])
    assert _evaluate(expr, "refs/tags/v2.7.11") is True
    assert _evaluate(expr, "refs/tags/v3.0.0") is True
    assert _evaluate(expr, "refs/heads/main") is False
    assert _evaluate(expr, "refs/heads/fix/anything") is False
    assert _evaluate(expr, "refs/tags/v1.0.0-rc.1") is False  # prereleases never


def test_main_pushes_still_publish_a_branch_tag():
    tags = _meta_step()["with"]["tags"]
    assert "type=ref,event=branch" in tags
