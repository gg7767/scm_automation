def financial_year_label(on_date):
    """Indian financial year label for a date, e.g. 2026-06-15 -> '26-27'
    (FY runs April to March)."""
    if on_date.month >= 4:
        start_year, end_year = on_date.year, on_date.year + 1
    else:
        start_year, end_year = on_date.year - 1, on_date.year
    return f"{start_year % 100:02d}-{end_year % 100:02d}"


def generate_po_number(model, site, on_date):
    """Return the next PO/{site.code}/{FY}/{seq} number for `site`/`on_date`'s
    financial year, based on the highest existing sequence for that prefix."""
    from django.db import transaction

    fy = financial_year_label(on_date)
    prefix = f"PO/{site.code}/{fy}/"
    with transaction.atomic():
        last = (
            model.objects.select_for_update()
            .filter(po_number__startswith=prefix)
            .order_by("-po_number")
            .first()
        )
        next_seq = 1
        if last:
            suffix = last.po_number[len(prefix):]
            if suffix.isdigit():
                next_seq = int(suffix) + 1
        return f"{prefix}{next_seq:04d}"
