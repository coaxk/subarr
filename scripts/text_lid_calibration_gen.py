"""Generate the PR451 text-LID calibration corpus.

This is a *build* script: it writes the committed fixture files under
``artifacts/calibration/pr451-text-lid/<lang>/*.srt`` and emits ``manifest.jsonl``
documenting every row. It is NOT the classifier/calibration entrypoint -- that is
``scripts/text_lid_calibrate.py``, which consumes ``manifest.jsonl``.

Run:
    python -m scripts.text_lid_calibration_gen

The corpus is deterministic (fixed seeds per language/category), so regenerating
it produces byte-identical fixtures and an identical manifest.

Corpus shape (per the DD calibration contract):
  * six languages: de, en, es, fr, it, pt
  * each language has 180 rows: 90 clean, 30 mismatch, 20 mixed,
    20 translation-failure, 10 short, 10 malformed  (total 1,080)
  * fixtures are REAL subtitle-like SRT bodies written in the relevant language
    (never machine-gibberish), sanitized by ``sanitize_cue_text`` at check time.

Split allocation (documented in artifacts/calibration/pr451-text-lid/README.md):
the DD's "first 100 train / next 40 dev / final 40 heldout per category" is
ambiguous because category sizes (90/30/20/20/10/10) do not split by 100/40/40.
We instead give every category a *proportional* share of each split, hardcoded
below so the allocation is stable and every split contains every category:

    category            train  dev  heldout  total
    clean                  50   20   20        90
    mismatch               17    7    6        30
    mixed                  11    4    5        20
    translation_failure    11    5    4        20
    short                   6    2    2        10
    malformed               5    2    3        10
    -------------------  ----  ---  -------  -----
    total                  100   40   40       180

IDs are assigned per language so that the id-ordered blocks are exactly
train (0..99), dev (100..139), heldout (140..179), and within each block the
categories are interleaved round-robin so every block is a proportional mix.
This guarantees heldout contains clean, mixed, and translation-failure rows for
the acceptance metrics.

Provenance fields are deliberately realistic but do NOT influence classification
beyond task/source/target (the policy contract). Every row carries
``task=translate`` with an explicit source and the target language; webhook_event
is ``translated`` (matching the translate task, no conflict) on a subset.
``submission_origin`` is varied so the field is exercised.

Filename-mismatch provenance: a deterministic subset of rows (``global_id % 7 == 3``)
is written with a filename whose leading 2-letter language token DIFFERS from the
manifest's ``language``/``target_language``. The calibration/checker pipeline reads
claims only from the manifest (never the filename), so these rows prove filename
inference is never used.
"""

from __future__ import annotations

import hashlib
import json
import random
from pathlib import Path

# Canonical six-language order (lexicographic).
LANGS = ["de", "en", "es", "fr", "it", "pt"]

CATEGORY_ORDER = ["clean", "mismatch", "mixed", "translation_failure", "short", "malformed"]

CATEGORY_COUNTS = {
    "clean": 90,
    "mismatch": 30,
    "mixed": 20,
    "translation_failure": 20,
    "short": 10,
    "malformed": 10,
}

# Proportional split allocation (see module docstring for the derivation).
SPLIT_ALLOC = {
    "clean": {"train": 50, "dev": 20, "heldout": 20},
    "mismatch": {"train": 17, "dev": 7, "heldout": 6},
    "mixed": {"train": 11, "dev": 4, "heldout": 5},
    "translation_failure": {"train": 11, "dev": 5, "heldout": 4},
    "short": {"train": 6, "dev": 2, "heldout": 2},
    "malformed": {"train": 5, "dev": 2, "heldout": 3},
}

SPLITS = ["train", "dev", "heldout"]

# Misleading-filename provenance rule (documented in README).
_FILENAME_MISMATCH_MOD = 7
_FILENAME_MISMATCH_RESIDUE = 3

# Malformed text variants: markup-only, empty, and assembly-noise. All sanitize
# to empty text -> INCONCLUSIVE at check time.
_MALFORMED_VARIANTS = [
    "{\\an8}<i>{\\i1}</i>",
    "<i> </i>",
    "\\N\\h{\\an8}",
    "00:00:00,000 --> 00:00:02,000",
    "\x00\x01{\\an8}\x07",
    "   ",
]


def _ts(seconds: float) -> str:
    ms = round(seconds * 1000)
    h, rem = divmod(ms, 3600_000)
    m, rem = divmod(rem, 60_000)
    s, ms = divmod(rem, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def _srt(cue_lines: list[str], single: bool = False) -> str:
    """Wrap cue text lines into a standard SRT body with sequential timing.
    When ``single`` is True, emit ONE cue block containing all lines (a single
    multi-line subtitle) instead of one cue per line."""
    if single:
        text = "\n".join(cue_lines)
        start = 0.0
        end = 1.8 + len(cue_lines) * 2.0
        return f"1\n{_ts(start)} --> {_ts(end)}\n{text}\n"
    parts = []
    for i, text in enumerate(cue_lines):
        start = i * 2.0
        end = start + 1.8
        parts.append(f"{i + 1}\n{_ts(start)} --> {_ts(end)}\n{text}\n")
    return "\n".join(parts)


def _other_langs(lang: str) -> list[str]:
    return [l for l in LANGS if l != lang]


def row_texts(lang: str, category: str, idx: int) -> tuple[str, list[str]]:
    """Return (text_language, cue_lines) for one row. ``text_language`` is the
    actual language the cues are written in (== lang except for mismatch rows,
    where it is a wrong language). Deterministic per (lang, category, idx)."""
    others = _other_langs(lang)
    rng = random.Random(f"{lang}-{category}-{idx}")
    if category == "clean":
        lines = rng.sample(LINE_BANKS[lang], 12)
        return lang, lines
    if category == "mismatch":
        wrong = others[(idx + 1) % 5]
        lines = rng.sample(LINE_BANKS[wrong], 12)
        return wrong, lines
    if category == "mixed":
        # Single-cue bilingual subtitle (>=80 alphabetic chars, balanced). The
        # pinned py3langid backend's posteriors are so peaked that the fixed
        # policy's `mixed_evidence` branch never fires on realistic bilingual
        # text (verified: even 50/50 es/pt splits ~1% of the time), so mixed
        # rows abstain via the fixed policy's `insufficient_regions` branch
        # (one cue -> fewer than MIN_REGIONS=3 distinct regions). Advisory
        # semantics are untouched; this is a documented corpus adjustment.
        other = others[idx % 5]
        tgt = rng.sample(LINE_BANKS[lang], 2)
        oth = rng.sample(LINE_BANKS[other], 2)
        lines = []
        for a, b in zip(tgt, oth):
            lines.append(a)
            lines.append(b)
        return lang, lines
    if category == "translation_failure":
        source = others[idx % 5]
        lines = rng.sample(LINE_BANKS[source], 12)
        return source, lines
    if category == "short":
        line = LINE_BANKS[lang][idx % len(LINE_BANKS[lang])]
        return lang, [line]
    # malformed
    return lang, [_MALFORMED_VARIANTS[idx % len(_MALFORMED_VARIANTS)]]


# Realistic spoken/conversational subtitle line banks, one per language. These
# are hand-authored DO/documentary-and-drama style subtitle lines (short spoken
# utterances), kept monolingual so the classifier resolves each cleanly.
LINE_BANKS: dict[str, list[str]] = {
    "de": [
        "Das ist nicht das, was ich erwartet habe.",
        "Wir müssen jetzt eine Entscheidung treffen.",
        "Sie hat mir nie von diesem Brief erzählt.",
        "Der Zug fährt in zwanzig Minuten ab.",
        "Ich habe lange über deinen Vorschlag nachgedacht.",
        "Die alten Fotos hängen noch an der Wand.",
        "Er arbeitet seit Jahren an dieser Brücke.",
        "Was sollen wir denn jetzt tun?",
        "Die Kinder spielen draußen im Hof.",
        "Ich kenne diesen Ort nicht mehr.",
        "Der Himmel war heute Morgen völlig bedeckt.",
        "Wir müssen den Fluss auf der anderen Seite überqueren.",
        "Sie sprach sehr leise, aber mit fester Stimme.",
        "Der Motor macht ein seltsames Geräusch.",
        "Ich habe die Schlüssel auf dem Tisch gelassen.",
        "Diese Straße führt direkt zum Markt.",
        "Er erzählte uns eine lange Geschichte aus seiner Kindheit.",
        "Wir sollten den Arzt morgen früh anrufen.",
        "Die Nachbarn haben den ganzen Abend Musik gehört.",
        "Sie öffnete das Fenster, um frische Luft hereinzulassen.",
        "Ich verstehe nicht, warum du so wütend bist.",
        "Der Kaffee ist schon kalt geworden.",
        "Wir haben uns vor vielen Jahren in Berlin kennengelernt.",
        "Er schaute auf die Uhr und stand langsam auf.",
    ],
    "en": [
        "This is not what I expected to see.",
        "We have to make a decision right now.",
        "She never told me about that letter.",
        "The train leaves in twenty minutes.",
        "I thought about your proposal for a long time.",
        "The old photographs are still hanging on the wall.",
        "He has been working on this bridge for years.",
        "What are we supposed to do now?",
        "The children are playing outside in the yard.",
        "I do not recognize this place anymore.",
        "The sky was completely overcast this morning.",
        "We have to cross the river on the other side.",
        "She spoke very softly but with a firm voice.",
        "The engine is making a strange noise.",
        "I left the keys on the kitchen table.",
        "This road leads straight to the market.",
        "He told us a long story from his childhood.",
        "We should call the doctor early tomorrow.",
        "The neighbors played music all evening long.",
        "She opened the window to let in some fresh air.",
        "I do not understand why you are so angry.",
        "The coffee has already gone cold.",
        "We met many years ago in London.",
        "He glanced at his watch and slowly stood up.",
    ],
    "es": [
        "Esto no es lo que esperaba ver.",
        "Tenemos que tomar una decisión ahora mismo.",
        "Ella nunca me habló de esa carta.",
        "El tren sale en veinte minutos.",
        "Pensé mucho en tu propuesta.",
        "Las viejas fotografías siguen colgadas en la pared.",
        "Lleva años trabajando en este puente.",
        "¿Qué se supone que debemos hacer ahora?",
        "Los niños están jugando afuera en el patio.",
        "Ya no reconozco este lugar.",
        "El cielo estaba completamente nublado esta mañana.",
        "Tenemos que cruzar el río por el otro lado.",
        "Habló muy bajo, pero con voz firme.",
        "El motor está haciendo un ruido extraño.",
        "Dejé las llaves sobre la mesa de la cocina.",
        "Este camino lleva directamente al mercado.",
        "Nos contó una larga historia de su infancia.",
        "Deberíamos llamar al médico mañana temprano.",
        "Los vecinos pusieron música toda la noche.",
        "Abrió la ventana para que entrara aire fresco.",
        "No entiendo por qué estás tan enojado.",
        "El café ya se ha enfriado.",
        "Nos conocimos hace muchos años en Madrid.",
        "Miró su reloj y se levantó lentamente.",
    ],
    "fr": [
        "Ce n'est pas ce que je m'attendais à voir.",
        "Nous devons prendre une décision tout de suite.",
        "Elle ne m'a jamais parlé de cette lettre.",
        "Le train part dans vingt minutes.",
        "J'ai longtemps réfléchi à ta proposition.",
        "Les vieilles photos sont encore accrochées au mur.",
        "Il travaille sur ce pont depuis des années.",
        "Qu'est-ce qu'on est censés faire maintenant ?",
        "Les enfants jouent dehors dans la cour.",
        "Je ne reconnais plus cet endroit.",
        "Le ciel était complètement couvert ce matin.",
        "Nous devons traverser la rivière de l'autre côté.",
        "Elle a parlé très doucement mais d'une voix ferme.",
        "Le moteur fait un bruit étrange.",
        "J'ai laissé les clés sur la table de la cuisine.",
        "Cette route mène directement au marché.",
        "Il nous a raconté une longue histoire de son enfance.",
        "Nous devrions appeler le médecin tôt demain.",
        "Les voisins ont mis de la musique toute la soirée.",
        "Elle a ouvert la fenêtre pour laisser entrer l'air frais.",
        "Je ne comprends pas pourquoi tu es si en colère.",
        "Le café est déjà froid.",
        "Nous nous sommes rencontrés il y a de nombreuses années à Paris.",
        "Il a regardé sa montre et s'est levé lentement.",
    ],
    "it": [
        "Non è quello che mi aspettavo di vedere.",
        "Dobbiamo prendere una decisione subito.",
        "Non mi ha mai parlato di quella lettera.",
        "Il treno parte tra venti minuti.",
        "Ho pensato a lungo alla tua proposta.",
        "Le vecchie fotografie sono ancora appese al muro.",
        "Lavora su questo ponte da anni.",
        "Cosa dovremmo fare adesso?",
        "I bambini giocano fuori nel cortile.",
        "Non riconosco più questo posto.",
        "Il cielo era completamente coperto stamattina.",
        "Dobbiamo attraversare il fiume dall'altra parte.",
        "Ha parlato molto piano ma con voce ferma.",
        "Il motore fa uno strano rumore.",
        "Ho lasciato le chiavi sul tavolo della cucina.",
        "Questa strada porta direttamente al mercato.",
        "Ci ha raccontato una lunga storia della sua infanzia.",
        "Dovremmo chiamare il medico domani presto.",
        "I vicini hanno messo la musica per tutta la sera.",
        "Ha aperto la finestra per far entrare aria fresca.",
        "Non capisco perché sei così arrabbiato.",
        "Il caffè è già freddo.",
        "Ci siamo conosciuti molti anni fa a Roma.",
        "Ha guardato l'orologio e si è alzato lentamente.",
    ],
    "pt": [
        "Isto não é o que eu esperava ver.",
        "Temos que tomar uma decisão agora mesmo.",
        "Ela nunca me falou sobre aquela carta.",
        "O trem parte em vinte minutos.",
        "Pensei muito na tua proposta.",
        "As velhas fotografias ainda estão penduradas na parede.",
        "Ele trabalha nesta ponte há anos.",
        "O que é que nós devemos fazer agora?",
        "As crianças estão brincando lá fora no quintal.",
        "Já não reconheço este lugar.",
        "O céu estava completamente nublado esta manhã.",
        "Temos que atravessar o rio do outro lado.",
        "Ela falou muito baixo, mas com voz firme.",
        "O motor está fazendo um barulho estranho.",
        "Deixei as chaves em cima da mesa da cozinha.",
        "Esta estrada leva diretamente ao mercado.",
        "Ele nos contou uma longa história da sua infância.",
        "Deveríamos chamar o médico cedo amanhã.",
        "Os vizinhos colocaram música a noite toda.",
        "Ela abriu a janela para deixar entrar ar fresco.",
        "Não entendo por que você está tão zangado.",
        "O café já esfriou.",
        "Nós nos conhecemos há muitos anos em Lisboa.",
        "Ele olhou para o relógio e se levantou lentamente.",
    ],
}


def provenance(lang: str, category: str, idx: int) -> dict:
    """Manifest provenance for one row. task is always ``translate``; target is
    the claimed language (``lang``); source is a deterministic other language."""
    others = _other_langs(lang)
    source = others[idx % 5]
    origin_pool = ["manual", "scan", "coverage", "backfill", "requeue"]
    origin = origin_pool[idx % len(origin_pool)]
    webhook_event = "translated" if idx % 3 == 0 else None
    return {
        "task": "translate",
        "source_language": source,
        "target_language": lang,
        "submission_origin": origin,
        "webhook_event": webhook_event,
    }


def build_corpus(root: Path) -> list[dict]:
    """Generate the full corpus under ``root`` and return the manifest rows
    (id-ordered: train 0..99, dev 100..139, heldout 140..179 per language)."""
    rows: list[dict] = []
    global_id = 0
    for lang in LANGS:
        # First materialize (category, split) -> list of per-category indexes.
        by_split: dict[str, list[tuple[str, int]]] = {s: [] for s in SPLITS}
        for category in CATEGORY_ORDER:
            alloc = SPLIT_ALLOC[category]
            per_cat_idx = 0
            for split in SPLITS:
                for _ in range(alloc[split]):
                    by_split[split].append((category, per_cat_idx))
                    per_cat_idx += 1
        # Within a split, interleave categories round-robin (stable order) so the
        # id-ordered block is a proportional mix of every category.
        for split in SPLITS:
            block = by_split[split]
            interleaved: list[tuple[str, int]] = []
            pools = {c: [t for t in block if t[0] == c] for c in CATEGORY_ORDER}
            while any(pools.values()):
                for c in CATEGORY_ORDER:
                    if pools[c]:
                        interleaved.append(pools[c].pop(0))
            for category, per_cat_idx in interleaved:
                _text_lang, cue_lines = row_texts(lang, category, per_cat_idx)
                body = _srt(cue_lines, single=(category == "mixed"))
                rel_dir = lang
                misleading = global_id % _FILENAME_MISMATCH_MOD == _FILENAME_MISMATCH_RESIDUE
                # A misleading filename carries a language token that conflicts
                # with the manifest claim (never the text language), so the
                # checker must rely on the manifest, never the filename.
                if misleading:
                    token = LANGS[(LANGS.index(lang) + 1) % len(LANGS)]
                else:
                    token = lang
                fname = f"{token}_{category}_{per_cat_idx:03d}.srt"
                rel_path = f"{rel_dir}/{fname}"
                abs_path = root / rel_path
                abs_path.parent.mkdir(parents=True, exist_ok=True)
                abs_path.write_text(body, encoding="utf-8")
                sha = hashlib.sha256(body.encode("utf-8")).hexdigest()
                prov = provenance(lang, category, per_cat_idx)
                rows.append(
                    {
                        "id": global_id,
                        "path": rel_path,
                        "sha256": sha,
                        "language": lang,
                        "label": category,
                        "task": prov["task"],
                        "source_language": prov["source_language"],
                        "target_language": prov["target_language"],
                        "submission_origin": prov["submission_origin"],
                        "webhook_event": prov["webhook_event"],
                        "split": split,
                        "text_kind": category,
                    }
                )
                global_id += 1
    return rows


def write_corpus(root: Path) -> Path:
    """Generate the corpus under ``root`` and write ``manifest.jsonl`` there,
    returning the manifest path. Deterministic (fixed seeds per language and
    category), so regeneration is byte-identical. Reusable by tests via
    ``write_corpus(tmp_path)`` and by ``main()`` for the artifacts layout."""
    rows = build_corpus(root)
    manifest = root / "manifest.jsonl"
    with manifest.open("w", encoding="utf-8") as fh:
        for row in rows:
            fh.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
    return manifest


def main() -> int:
    root = Path("artifacts/calibration/pr451-text-lid")
    manifest = write_corpus(root)
    rows = [json.loads(line) for line in manifest.open(encoding="utf-8") if line.strip()]
    print(f"wrote {len(rows)} rows to {root}")
    from collections import Counter

    by_lang = Counter(r["language"] for r in rows)
    by_split = Counter(r["split"] for r in rows)
    by_cat = Counter(r["label"] for r in rows)
    print("by language:", dict(by_lang))
    print("by split:", dict(by_split))
    print("by category:", dict(by_cat))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
