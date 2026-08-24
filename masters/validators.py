from django.core.validators import RegexValidator

gstin_validator = RegexValidator(
    regex=r"^\d{2}[A-Z]{5}\d{4}[A-Z][1-9A-Z]Z[0-9A-Z]$",
    message="Enter a valid 15-character GSTIN (e.g. 36ABCDE1234F1Z5).",
)

pan_validator = RegexValidator(
    regex=r"^[A-Z]{5}\d{4}[A-Z]$",
    message="Enter a valid 10-character PAN (e.g. ABCDE1234F).",
)

ifsc_validator = RegexValidator(
    regex=r"^[A-Z]{4}0[A-Z0-9]{6}$",
    message="Enter a valid 11-character IFSC code (e.g. HDFC0001234).",
)
