from masters.numbering import financial_year_label, generate_document_number

__all__ = ["financial_year_label", "generate_po_number"]


def generate_po_number(model, site, on_date):
    return generate_document_number(model, "po_number", "PO", site.code, on_date, width=4)
