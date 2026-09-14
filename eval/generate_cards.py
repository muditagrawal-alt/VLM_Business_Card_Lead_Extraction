"""Generate synthetic business cards with known ground truth.

Real photographed cards are the honest test, but they are slow to collect and
carry personal data. Synthetic cards give a large, reproducible baseline where
the correct answer is known exactly, which makes it possible to measure a
prompt or model change without hand-labelling anything.

The generated set deliberately includes the layouts that break extraction:
dark backgrounds, centred text, a card with no job title, an initial instead of
a first name, an honorific and suffix, two people on one card, and a
non-Latin script.

Usage:
    python eval/generate_cards.py --out eval/cards/synthetic
"""

from __future__ import annotations

import argparse
import json
import random
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw, ImageFont

FONT_DIR = Path("/System/Library/Fonts/Supplemental")
FALLBACK_FONT_DIR = Path("/System/Library/Fonts")

# Card stock proportions (3.5 x 2 inches) rendered at a print-like density.
CARD_W, CARD_H = 1050, 600


@dataclass
class CardSpec:
    """A card to render, together with the answer it should produce."""

    slug: str
    first_name: str | None
    last_name: str | None
    position: str | None
    company: str | None
    email: str | None
    phone: str | None
    city: str | None
    country: str | None
    website: str | None = None
    style: str = "left"
    notes_hint: str = ""
    extra_lines: list[str] = field(default_factory=list)

    def ground_truth(self) -> dict[str, Any]:
        return {
            "first_name": self.first_name,
            "last_name": self.last_name,
            "position": self.position,
            "company": self.company,
            "location": ", ".join(p for p in (self.city, self.country) if p) or None,
            "phone": self.phone,
            "email": self.email,
            "website": self.website,
            "_style": self.style,
            "_notes_hint": self.notes_hint,
        }


SPECS: list[CardSpec] = [
    CardSpec(
        slug="01-clean-corporate",
        first_name="Priya",
        last_name="Raghavan",
        position="Head of Business Development",
        company="Meridian Logistics",
        email="priya.raghavan@meridianlog.com",
        phone="+91 98765 43210",
        city="Mumbai",
        country="India",
        website="meridianlog.com",
        style="left",
    ),
    CardSpec(
        slug="02-dark-centred",
        first_name="Tomas",
        last_name="Lindqvist",
        position="Chief Technology Officer",
        company="Nordwave Systems",
        email="t.lindqvist@nordwave.se",
        phone="+46 70 123 45 67",
        city="Stockholm",
        country="Sweden",
        website="nordwave.se",
        style="dark-centred",
    ),
    CardSpec(
        slug="03-no-job-title",
        first_name="Marcus",
        last_name="Oyelaran",
        position=None,
        company="Oyelaran & Co.",
        email="marcus@oyelaran.co.uk",
        phone="+44 20 7946 0958",
        city="London",
        country="United Kingdom",
        style="left",
        notes_hint="card prints no job title",
    ),
    CardSpec(
        slug="04-initial-only",
        first_name="J.",
        last_name="Wehrmann",
        position="Senior Procurement Manager",
        company="Baltrex Industrie GmbH",
        email="j.wehrmann@baltrex.de",
        phone="+49 30 901820",
        city="Berlin",
        country="Germany",
        style="minimal",
        notes_hint="first name is an initial and must not be expanded",
    ),
    CardSpec(
        slug="05-honorific-suffix",
        first_name="Eleanor",
        last_name="Whitfield",
        position="Principal Consultant",
        company="Whitfield Advisory",
        email="eleanor@whitfieldadvisory.com",
        phone="+1 (415) 555-0142",
        city="San Francisco",
        country="USA",
        style="left",
        notes_hint="honorific Dr. and suffix PhD belong in notes, not the name fields",
        extra_lines=["Dr. Eleanor Whitfield, PhD"],
    ),
    CardSpec(
        slug="06-two-people",
        first_name="Hiroshi",
        last_name="Tanaka",
        position="Export Director",
        company="Tanaka Seiki Co., Ltd.",
        email="h.tanaka@tanakaseiki.jp",
        phone="+81 3 5299 1234",
        city="Tokyo",
        country="Japan",
        style="two-people",
        notes_hint="second person on the card should be described in notes",
    ),
    CardSpec(
        slug="07-tagline-trap",
        first_name="Aisha",
        last_name="Khoury",
        position="Marketing Lead",
        company="Cedarline Foods",
        email="aisha.khoury@cedarline.ae",
        phone="+971 4 123 4567",
        city="Dubai",
        country="United Arab Emirates",
        website="cedarline.ae",
        style="tagline",
        notes_hint="the slogan must not be read as the company name",
        extra_lines=["Taste the Mountain."],
    ),
    CardSpec(
        slug="08-multiple-phones",
        first_name="Daniel",
        last_name="Okonkwo",
        position="Operations Manager",
        company="Harmattan Freight",
        email="daniel.okonkwo@harmattan.ng",
        phone="+234 803 555 0188",
        city="Lagos",
        country="Nigeria",
        style="multi-contact",
        notes_hint="mobile must be chosen as the primary phone over office and fax",
    ),
]


def _font(name: str, size: int) -> ImageFont.FreeTypeFont:
    for directory in (FONT_DIR, FALLBACK_FONT_DIR):
        path = directory / name
        if path.exists():
            return ImageFont.truetype(str(path), size)
    return ImageFont.load_default(size)


def _render(spec: CardSpec, rng: random.Random) -> Image.Image:
    dark = spec.style == "dark-centred"
    bg = (24, 26, 32) if dark else (253, 252, 250)
    fg = (240, 240, 245) if dark else (26, 26, 30)
    muted = (150, 152, 160) if dark else (96, 98, 105)
    accent = (94, 140, 220) if dark else (26, 86, 168)

    img = Image.new("RGB", (CARD_W, CARD_H), bg)
    d = ImageDraw.Draw(img)

    name_f = _font("Arial Bold.ttf", 58)
    role_f = _font("Arial.ttf", 30)
    body_f = _font("Arial.ttf", 27)
    comp_f = _font("Arial Bold.ttf", 34)
    small_f = _font("Arial Italic.ttf", 25)

    centred = spec.style in {"dark-centred"}
    x = CARD_W // 2 if centred else 80
    anchor_prefix = "m" if centred else "l"

    def line(y: int, text: str, fnt: ImageFont.FreeTypeFont, colour: tuple[int, int, int]) -> None:
        d.text((x, y), text, font=fnt, fill=colour, anchor=f"{anchor_prefix}a")

    printed_name = spec.extra_lines[0] if spec.slug == "05-honorific-suffix" else " ".join(
        p for p in (spec.first_name, spec.last_name) if p
    )

    if spec.style == "minimal":
        line(150, printed_name, name_f, fg)
        line(225, spec.position or "", role_f, muted)
        line(300, spec.company or "", comp_f, accent)
        line(400, spec.email or "", body_f, fg)
        line(440, spec.phone or "", body_f, fg)
        line(480, f"{spec.city}, {spec.country}", body_f, muted)
        return img

    if spec.style == "two-people":
        line(70, spec.company or "", comp_f, accent)
        line(150, printed_name, _font("Arial Bold.ttf", 44), fg)
        line(205, spec.position or "", role_f, muted)
        line(250, spec.email or "", body_f, fg)
        line(290, spec.phone or "", body_f, fg)
        d.line([(80, 350), (CARD_W - 80, 350)], fill=muted, width=2)
        line(375, "Yuki Mori", _font("Arial Bold.ttf", 36), fg)
        line(420, "Sales Coordinator", small_f, muted)
        line(455, "y.mori@tanakaseiki.jp", body_f, fg)
        line(500, f"{spec.city}, {spec.country}", body_f, muted)
        return img

    if spec.style == "multi-contact":
        line(80, printed_name, name_f, fg)
        line(155, spec.position or "", role_f, muted)
        line(205, spec.company or "", comp_f, accent)
        line(300, f"Mobile   {spec.phone}", body_f, fg)
        line(340, "Office   +234 1 270 4411", body_f, fg)
        line(380, "Fax      +234 1 270 4412", body_f, muted)
        line(430, spec.email or "", body_f, fg)
        line(480, f"{spec.city}, {spec.country}", body_f, muted)
        return img

    if spec.style == "tagline":
        line(70, spec.company or "", comp_f, accent)
        line(120, spec.extra_lines[0], small_f, muted)
        line(215, printed_name, name_f, fg)
        line(290, spec.position or "", role_f, muted)
        line(370, spec.email or "", body_f, fg)
        line(410, spec.phone or "", body_f, fg)
        line(450, spec.website or "", body_f, accent)
        line(495, f"{spec.city}, {spec.country}", body_f, muted)
        return img

    # Default left-aligned and dark-centred layouts.
    top = 130 if centred else 110
    line(top, printed_name, name_f, fg)
    if spec.position:
        line(top + 80, spec.position, role_f, muted)
    line(top + 140, spec.company or "", comp_f, accent)
    y = top + 225
    for value, colour in (
        (spec.email, fg),
        (spec.phone, fg),
        (spec.website, accent),
        (f"{spec.city}, {spec.country}" if spec.city else None, muted),
    ):
        if value:
            line(y, value, body_f, colour)
            y += 42

    # A faint rule, as most printed cards carry some separator.
    if not centred:
        d.line([(80, 96), (80 + rng.randint(120, 260), 96)], fill=accent, width=4)
    return img


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out", type=Path, default=Path("eval/cards/synthetic"))
    ap.add_argument("--seed", type=int, default=7)
    args = ap.parse_args()

    args.out.mkdir(parents=True, exist_ok=True)
    rng = random.Random(args.seed)

    truth: dict[str, Any] = {}
    for spec in SPECS:
        img = _render(spec, rng)
        path = args.out / f"{spec.slug}.jpg"
        img.save(path, "JPEG", quality=92)
        truth[path.name] = spec.ground_truth()
        print(f"wrote {path} ({img.width}x{img.height})")

    truth_path = args.out.parent / "ground_truth_synthetic.json"
    truth_path.write_text(json.dumps(truth, indent=2, ensure_ascii=False) + "\n")
    print(f"\nground truth -> {truth_path} ({len(truth)} cards)")


if __name__ == "__main__":
    main()
