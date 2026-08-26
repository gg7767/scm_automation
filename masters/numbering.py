from django.db import transaction


def financial_year_label(on_date):
    """Indian financial year label for a date, e.g. 2026-06-15 -> '26-27'
    (FY runs April to March)."""
    if on_date.month >= 4:
        start_year, end_year = on_date.year, on_date.year + 1
    else:
        start_year, end_year = on_date.year - 1, on_date.year
    return f"{start_year % 100:02d}-{end_year % 100:02d}"


def generate_document_number(model, field_name, prefix, scope_code, on_date, width=4):
    """Return the next `{prefix}/{scope_code}/{FY}/{seq}` value for
    `model.field_name`, based on the highest existing sequence for that
    prefix. Used for all document numbering (PO, IND, GRN, BILL, PAY, TRP,
    ISS, ...) so every doc type shares one race-safe implementation."""
    fy = financial_year_label(on_date)
    doc_prefix = f"{prefix}/{scope_code}/{fy}/"
    with transaction.atomic():
        filter_kwargs = {f"{field_name}__startswith": doc_prefix}
        last = (
            model.objects.select_for_update()
            .filter(**filter_kwargs)
            .order_by(f"-{field_name}")
            .first()
        )
        next_seq = 1
        if last:
            existing = getattr(last, field_name)
            suffix = existing[len(doc_prefix):]
            if suffix.isdigit():
                next_seq = int(suffix) + 1
        return f"{doc_prefix}{next_seq:0{width}d}"
