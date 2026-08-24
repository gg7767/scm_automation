from decimal import Decimal

_ONES = [
    "", "One", "Two", "Three", "Four", "Five", "Six", "Seven", "Eight", "Nine",
    "Ten", "Eleven", "Twelve", "Thirteen", "Fourteen", "Fifteen", "Sixteen",
    "Seventeen", "Eighteen", "Nineteen",
]
_TENS = [
    "", "", "Twenty", "Thirty", "Forty", "Fifty", "Sixty", "Seventy", "Eighty", "Ninety",
]


def _two_digit_words(n):
    if n < 20:
        return _ONES[n]
    tens, ones = divmod(n, 10)
    return f"{_TENS[tens]} {_ONES[ones]}".strip()


def _three_digit_words(n):
    hundreds, rest = divmod(n, 100)
    parts = []
    if hundreds:
        parts.append(f"{_ONES[hundreds]} Hundred")
    if rest:
        parts.append(_two_digit_words(rest))
    return " ".join(parts)


def number_to_indian_words(value):
    """Convert an integer/Decimal rupee amount to words using the Indian
    numbering system (thousand/lakh/crore), e.g. 4864 -> 'Four Thousand
    Eight Hundred Sixty Four'."""
    n = int(Decimal(value).to_integral_value())
    if n == 0:
        return "Zero"

    crore, n = divmod(n, 10_000_000)
    lakh, n = divmod(n, 100_000)
    thousand, n = divmod(n, 1_000)
    hundred = n

    parts = []
    if crore:
        parts.append(f"{_three_digit_words(crore)} Crore")
    if lakh:
        parts.append(f"{_three_digit_words(lakh)} Lakh")
    if thousand:
        parts.append(f"{_three_digit_words(thousand)} Thousand")
    if hundred:
        parts.append(_three_digit_words(hundred))
    return " ".join(parts).strip()


def amount_in_words(value):
    rupees = int(Decimal(value).to_integral_value(rounding="ROUND_DOWN"))
    paise = int((Decimal(value) - rupees) * 100)
    words = f"Rupees {number_to_indian_words(rupees)}"
    if paise:
        words += f" and {number_to_indian_words(paise)} Paise"
    return words + " Only"


def gst_breakup(vendor_state, company_state, gst_amount):
    """Return (cgst, sgst, igst) for a total GST amount, based on whether
    the vendor's state matches the company's home state."""
    gst_amount = Decimal(gst_amount)
    if vendor_state and company_state and vendor_state == company_state:
        half = (gst_amount / 2).quantize(Decimal("0.01"))
        return half, gst_amount - half, Decimal("0.00")
    return Decimal("0.00"), Decimal("0.00"), gst_amount
