"""Excel and CSV export.

The workbook is the deliverable most users actually keep, so it is built to be
usable in Excel rather than merely valid: a frozen filtered header, widths that
fit the content, phone numbers stored as text, mailto links on addresses, and
amber shading on values the extractor was unsure about.
"""

from __future__ import annotations

import csv
import io
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.worksheet import Worksheet

if TYPE_CHECKING:
    from collections.abc import Sequence
    from uuid import UUID

    from app.models import Lead, Task

# (header, attribute, width)
COLUMNS: tuple[tuple[str, str, int], ...] = (
    ("#", "_row", 5),
    ("First Name", "first_name", 16),
    ("Last Name", "last_name", 18),
    ("Position", "position", 30),
    ("Company", "company", 28),
    ("Location", "location", 26),
    ("Phone", "phone", 20),
    ("Email", "email", 32),
    ("Website", "website", 24),
    ("Address", "_address", 34),
    ("Other Phones", "_extra_phones", 24),
    ("Other Emails", "_extra_emails", 28),
    ("Confidence", "_confidence", 12),
    ("Duplicate", "_duplicate", 11),
    ("Source File", "_source", 24),
    ("Extracted At", "_extracted_at", 20),
)

# The seven required fields; their confidence drives the amber shading.
SCORED_FIELDS = (
    "first_name",
    "last_name",
    "position",
    "company",
    "location",
    "phone",
    "email",
)

LOW_CONFIDENCE = 0.8

# What a worksheet cell can hold in this export.
type CellValue = str | int | float | None

_HEADER_FILL = PatternFill("solid", fgColor="1F3864")
_HEADER_FONT = Font(color="FFFFFF", bold=True)
_AMBER_FILL = PatternFill("solid", fgColor="FFF2CC")
_LINK_FONT = Font(color="0563C1", underline="single")


def _address_line(lead: Lead) -> str:
    parts = lead.address or {}
    ordered = ("street", "city", "state", "postal_code", "country")
    return ", ".join(str(parts[k]) for k in ordered if parts.get(k))


def _extra_phones(lead: Lead) -> str:
    return ", ".join(
        f"{entry.get('number')} ({entry.get('type')})"
        for entry in (lead.extra_phones or [])
        if entry.get("number")
    )


def _mean_confidence(lead: Lead) -> float | None:
    scores = [lead.confidence.get(f) for f in SCORED_FIELDS] if lead.confidence else []
    present = [s for s in scores if s is not None]
    return sum(present) / len(present) if present else None


def _has_flagged_field(lead: Lead) -> bool:
    """True when a field *that has a value* is below the review threshold.

    The summary counts rows this way rather than by mean confidence. A card
    with six confident fields and one doubtful one averages comfortably above
    the threshold, so a mean-based count reported "0 flagged" while a cell was
    visibly shaded amber — telling the reader the opposite of what the sheet
    showed.

    Fields that are absent are excluded. A missing field scores zero, but a
    card that simply does not print an address is not asking to be checked,
    and counting it made the workbook claim two rows needed review where the
    table showed one. The table's rule is the correct one, and this now
    matches it.
    """
    if not lead.confidence:
        return False
    return any(
        getattr(lead, field, None) is not None
        and (score := lead.confidence.get(field)) is not None
        and score < LOW_CONFIDENCE
        for field in SCORED_FIELDS
    )


def _cell_values(lead: Lead, index: int, filenames: dict[UUID, str]) -> dict[str, CellValue]:
    mean = _mean_confidence(lead)
    return {
        "_row": index,
        "_address": _address_line(lead),
        "_extra_phones": _extra_phones(lead),
        "_extra_emails": ", ".join(lead.extra_emails or []),
        "_confidence": round(mean, 2) if mean is not None else None,
        "_duplicate": "yes" if lead.is_duplicate_of else "",
        "_source": filenames.get(lead.image_id, ""),
        "_extracted_at": lead.created_at.strftime("%Y-%m-%d %H:%M"),
    }


def build_workbook(
    leads: Sequence[Lead],
    *,
    job_id: UUID,
    filenames: dict[UUID, str] | None = None,
    tasks: Sequence[Task] = (),
) -> bytes:
    """Render the leads and a provenance summary into an xlsx file."""
    filenames = filenames or {}
    workbook = Workbook()
    sheet = workbook.active
    if not isinstance(sheet, Worksheet):  # pragma: no cover - openpyxl always gives one
        raise RuntimeError("could not create the leads worksheet")
    sheet.title = "Leads"

    for column_index, (header, _, width) in enumerate(COLUMNS, start=1):
        cell = sheet.cell(row=1, column=column_index, value=header)
        cell.fill = _HEADER_FILL
        cell.font = _HEADER_FONT
        cell.alignment = Alignment(vertical="center")
        sheet.column_dimensions[get_column_letter(column_index)].width = width

    for row_offset, lead in enumerate(leads):
        row = row_offset + 2
        derived = _cell_values(lead, row_offset + 1, filenames)

        for column_index, (_, attribute, _width) in enumerate(COLUMNS, start=1):
            value: CellValue = (
                derived[attribute] if attribute.startswith("_") else getattr(lead, attribute, None)
            )
            cell = sheet.cell(row=row, column=column_index, value=value)

            if attribute in {"phone", "_extra_phones"} and value:
                # Without this Excel reads "+919876543210" as a formula or
                # strips the leading plus, corrupting the number on open.
                cell.number_format = "@"
            if attribute == "email" and value:
                cell.hyperlink = f"mailto:{value}"
                cell.font = _LINK_FONT
            if attribute in SCORED_FIELDS and lead.confidence:
                score = lead.confidence.get(attribute)
                if score is not None and score < LOW_CONFIDENCE:
                    # Shade rather than hide: the value may well be right, but
                    # it is the one a human should glance at first.
                    cell.fill = _AMBER_FILL

    last_column = get_column_letter(len(COLUMNS))
    sheet.freeze_panes = "A2"
    sheet.auto_filter.ref = f"A1:{last_column}{max(len(leads) + 1, 1)}"

    _add_summary(workbook, leads=leads, job_id=job_id, tasks=tasks)

    buffer = io.BytesIO()
    workbook.save(buffer)
    return buffer.getvalue()


def _add_summary(
    workbook: Workbook,
    *,
    leads: Sequence[Lead],
    job_id: UUID,
    tasks: Sequence[Task],
) -> None:
    """Record how the numbers were produced, not just what they are.

    Which model read which card, and how long it took, is what makes an
    exported list auditable later.
    """
    sheet = workbook.create_sheet("Summary")
    sheet.column_dimensions["A"].width = 30
    sheet.column_dimensions["B"].width = 46

    by_tier: dict[str, int] = {}
    latencies: list[int] = []
    models: set[str] = set()
    for task in tasks:
        if task.provider is not None:
            by_tier[task.provider.value] = by_tier.get(task.provider.value, 0) + 1
        if task.latency_ms is not None:
            latencies.append(task.latency_ms)
        if task.model:
            models.add(task.model)

    low_confidence = sum(1 for lead in leads if _has_flagged_field(lead))

    rows: list[tuple[str, CellValue]] = [
        ("Job", str(job_id)),
        ("Exported at (UTC)", datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S")),
        ("Leads", len(leads)),
        ("Rows with a field to check", low_confidence),
        ("Duplicates flagged", sum(1 for lead in leads if lead.is_duplicate_of)),
        ("Models used", ", ".join(sorted(models)) or "-"),
        (
            "Cards per tier",
            ", ".join(f"{tier}: {count}" for tier, count in sorted(by_tier.items())) or "-",
        ),
        (
            "Mean latency per card",
            f"{sum(latencies) / len(latencies) / 1000:.1f} s" if latencies else "-",
        ),
    ]

    for row_index, (label, value) in enumerate(rows, start=1):
        sheet.cell(row=row_index, column=1, value=label).font = Font(bold=True)
        sheet.cell(row=row_index, column=2, value=value)


def build_csv(leads: Sequence[Lead], *, filenames: dict[UUID, str] | None = None) -> bytes:
    """The same rows as CSV.

    Encoded with a UTF-8 BOM because Excel otherwise renders non-ASCII names
    as mojibake, which is the whole point of exporting a lead list.
    """
    filenames = filenames or {}
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\r\n")
    writer.writerow([header for header, _, _ in COLUMNS])

    for index, lead in enumerate(leads, start=1):
        derived = _cell_values(lead, index, filenames)
        writer.writerow(
            [
                derived[attribute]
                if attribute.startswith("_")
                else (getattr(lead, attribute, None) or "")
                for _, attribute, _w in COLUMNS
            ]
        )

    return b"\xef\xbb\xbf" + buffer.getvalue().encode("utf-8")


def export_filename(job_id: UUID, extension: str) -> str:
    stamp = datetime.now(UTC).strftime("%Y%m%d")
    return f"leads_{str(job_id)[:8]}_{stamp}.{extension}"
