"""Measure extraction accuracy and latency against known-answer cards.

Every claim the README makes about this system should come from this script
rather than from impressions. It runs a chosen inference tier over a card set,
normalises the output exactly as production does, and compares field by field
against ground truth.

Comparison rules, chosen so the score reflects usefulness rather than
punctuation:

- Phone numbers are compared in E.164 form, so printed grouping is irrelevant.
- Emails and websites are compared case-insensitively.
- Names, positions and companies use a similarity threshold, so "Meridian
  Logistics" matching "Meridian Logistics." is a pass while a different company
  is not.
- A field that should be null and is null counts as correct. Getting an absent
  field right matters: inventing a job title is a failure mode this set
  deliberately probes.

Usage:
    python eval/run_eval.py --tier cpu --base-url http://127.0.0.1:8081/v1 \
        --model Qwen3VL-4B-Instruct-Q4_K_M --max-edge 768
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import io
import json
import statistics
import sys
import time
from dataclasses import dataclass
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any

from PIL import Image, ImageOps

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "backend"))

from app.models.enums import ProviderTier  # noqa: E402
from app.services.normalisation import (  # noqa: E402
    normalise,
    normalise_email,
    normalise_phone,
    normalise_website,
)
from app.vlm.openai_compat import OpenAICompatProvider  # noqa: E402
from app.vlm.provider import VLMError  # noqa: E402

# Fields the assignment requires, which are therefore what the score reports.
REQUIRED_FIELDS = (
    "first_name",
    "last_name",
    "position",
    "company",
    "location",
    "phone",
    "email",
)
FUZZY_FIELDS = {"first_name", "last_name", "position", "company", "location"}
SIMILARITY_THRESHOLD = 0.90


@dataclass(slots=True)
class CardResult:
    card: str
    ok: bool
    latency_ms: int
    output_mode: str
    fields: dict[str, bool]
    got: dict[str, Any]
    expected: dict[str, Any]
    error: str | None = None


def to_data_url(path: Path, max_edge: int) -> tuple[str, int, int]:
    """Preprocess exactly as the ingestion pipeline does, then encode."""
    with Image.open(path) as img:
        # Honour EXIF rotation, then discard metadata by re-encoding.
        img = ImageOps.exif_transpose(img).convert("RGB")
        img.thumbnail((max_edge, max_edge), Image.Resampling.LANCZOS)
        buf = io.BytesIO()
        img.save(buf, "JPEG", quality=85, optimize=True)
        width, height = img.width, img.height
    encoded = base64.b64encode(buf.getvalue()).decode()
    return f"data:image/jpeg;base64,{encoded}", width, height


def _similar(a: str, b: str) -> bool:
    return SequenceMatcher(None, a.casefold(), b.casefold()).ratio() >= SIMILARITY_THRESHOLD


def compare_field(name: str, got: Any, expected: Any) -> bool:
    """True when the extracted value is as useful as the expected one."""
    if expected is None:
        # Correctly leaving an absent field blank is a pass; inventing is not.
        return got is None
    if got is None:
        return False

    got_s, expected_s = str(got).strip(), str(expected).strip()
    if name == "phone":
        return normalise_phone(got_s) == normalise_phone(expected_s)
    if name == "email":
        return normalise_email(got_s) == normalise_email(expected_s)
    if name == "website":
        return normalise_website(got_s) == normalise_website(expected_s)
    if name in FUZZY_FIELDS:
        return _similar(got_s, expected_s)
    return got_s.casefold() == expected_s.casefold()


async def run_card(
    provider: OpenAICompatProvider, path: Path, expected: dict[str, Any], max_edge: int
) -> CardResult:
    data_url, _, _ = to_data_url(path, max_edge)
    started = time.perf_counter()
    try:
        result = await provider.extract(data_url)
    except VLMError as exc:
        return CardResult(
            card=path.name,
            ok=False,
            latency_ms=int((time.perf_counter() - started) * 1000),
            output_mode="-",
            fields=dict.fromkeys(REQUIRED_FIELDS, False),
            got={},
            expected=expected,
            error=str(exc),
        )

    lead = normalise(result.extraction)
    got = {f: getattr(lead, f) for f in REQUIRED_FIELDS}
    fields = {f: compare_field(f, got[f], expected.get(f)) for f in REQUIRED_FIELDS}
    return CardResult(
        card=path.name,
        ok=True,
        latency_ms=result.latency_ms,
        output_mode=result.output_mode.value,
        fields=fields,
        got=got,
        expected={f: expected.get(f) for f in REQUIRED_FIELDS},
    )


def summarise(results: list[CardResult]) -> dict[str, Any]:
    per_field: dict[str, float] = {}
    for f in REQUIRED_FIELDS:
        hits = sum(1 for r in results if r.fields.get(f))
        per_field[f] = hits / len(results) if results else 0.0

    total_fields = len(results) * len(REQUIRED_FIELDS)
    total_hits = sum(sum(1 for v in r.fields.values() if v) for r in results)
    latencies = sorted(r.latency_ms for r in results if r.ok)

    return {
        "cards": len(results),
        "cards_failed": sum(1 for r in results if not r.ok),
        "field_accuracy": total_hits / total_fields if total_fields else 0.0,
        "per_field": per_field,
        "perfect_cards": sum(1 for r in results if all(r.fields.values())),
        "latency_p50_ms": statistics.median(latencies) if latencies else 0,
        "latency_p95_ms": (
            latencies[max(0, int(len(latencies) * 0.95) - 1)] if latencies else 0
        ),
        "latency_mean_ms": int(statistics.fmean(latencies)) if latencies else 0,
        "output_modes": {
            mode: sum(1 for r in results if r.output_mode == mode)
            for mode in sorted({r.output_mode for r in results})
        },
    }


def print_report(label: str, results: list[CardResult], summary: dict[str, Any]) -> None:
    print(f"\n{'=' * 78}\n{label}\n{'=' * 78}")
    header = f"{'card':28} {'ms':>6}  " + "  ".join(f[:4] for f in REQUIRED_FIELDS)
    print(header)
    print("-" * len(header))
    for r in results:
        marks = "  ".join(("  ok" if r.fields[f] else "  XX") for f in REQUIRED_FIELDS)
        print(f"{r.card:28} {r.latency_ms:>6}  {marks}")

    print(f"\nfield accuracy : {summary['field_accuracy']:.1%}")
    print(f"perfect cards  : {summary['perfect_cards']}/{summary['cards']}")
    print(f"failed cards   : {summary['cards_failed']}")
    print(
        f"latency        : p50 {summary['latency_p50_ms'] / 1000:.1f}s  "
        f"p95 {summary['latency_p95_ms'] / 1000:.1f}s  "
        f"mean {summary['latency_mean_ms'] / 1000:.1f}s"
    )
    print(f"output modes   : {summary['output_modes']}")
    print("\nper field:")
    for f, acc in summary["per_field"].items():
        bar = "#" * int(acc * 20)
        print(f"  {f:14} {acc:6.1%} {bar}")

    misses = [
        (r.card, f, r.got.get(f), r.expected.get(f))
        for r in results
        for f in REQUIRED_FIELDS
        if not r.fields[f]
    ]
    if misses:
        print("\nmisses (got -> expected):")
        for card, f, got, exp in misses:
            print(f"  {card:28} {f:12} {got!r} -> {exp!r}")
    for r in results:
        if r.error:
            print(f"\nerror on {r.card}: {r.error}")


async def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--tier", choices=[t.value for t in ProviderTier], default="cpu")
    ap.add_argument("--base-url", default="http://127.0.0.1:8081/v1")
    ap.add_argument("--model", default="Qwen3VL-4B-Instruct-Q4_K_M")
    ap.add_argument("--api-key", default="")
    ap.add_argument("--max-edge", type=int, default=768)
    ap.add_argument("--timeout", type=float, default=300.0)
    ap.add_argument("--cards", type=Path, default=REPO_ROOT / "eval/cards/synthetic")
    ap.add_argument("--truth", type=Path, default=None)
    ap.add_argument("--json-out", type=Path, default=None)
    ap.add_argument("--label", default="")
    args = ap.parse_args()

    truth_path = args.truth or args.cards.parent / "ground_truth_synthetic.json"
    truth: dict[str, Any] = json.loads(truth_path.read_text())

    provider = OpenAICompatProvider(
        tier=ProviderTier(args.tier),
        base_url=args.base_url,
        model=args.model,
        api_key=args.api_key,
        timeout_s=args.timeout,
    )
    if not await provider.health():
        print(f"provider at {args.base_url} is not responding", file=sys.stderr)
        await provider.aclose()
        raise SystemExit(1)

    cards = sorted(p for p in args.cards.glob("*.jpg") if p.name in truth)
    print(f"running {len(cards)} cards against {args.model} at max_edge={args.max_edge}")

    results: list[CardResult] = []
    for path in cards:
        result = await run_card(provider, path, truth[path.name], args.max_edge)
        state = "ok " if all(result.fields.values()) else "   "
        print(f"  {state}{path.name:28} {result.latency_ms:>6} ms  {result.output_mode}")
        results.append(result)
    await provider.aclose()

    summary = summarise(results)
    label = args.label or f"{args.model} @ {args.max_edge}px ({args.tier})"
    print_report(label, results, summary)

    if args.json_out:
        payload = {
            "label": label,
            "model": args.model,
            "tier": args.tier,
            "max_edge": args.max_edge,
            "summary": summary,
            "cards": [
                {
                    "card": r.card,
                    "latency_ms": r.latency_ms,
                    "output_mode": r.output_mode,
                    "fields": r.fields,
                    "got": r.got,
                    "expected": r.expected,
                    "error": r.error,
                }
                for r in results
            ],
        }
        args.json_out.parent.mkdir(parents=True, exist_ok=True)
        args.json_out.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n")
        print(f"\nwrote {args.json_out}")


if __name__ == "__main__":
    asyncio.run(main())
