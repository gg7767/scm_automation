from django import forms
from django.forms import modelformset_factory

from billing.models import BillRemark, Payment, PaymentAllocation, VendorBill, VendorBillLine
from masters.forms import TailwindFormMixin


class VendorBillForm(TailwindFormMixin, forms.ModelForm):
    class Meta:
        model = VendorBill
        fields = [
            "vendor_invoice_number", "vendor_invoice_date", "invoice_scan",
            "other_charges", "round_off", "due_date",
        ]
        widgets = {
            "vendor_invoice_date": forms.DateInput(attrs={"type": "date"}),
            "due_date": forms.DateInput(attrs={"type": "date"}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["due_date"].required = False
        self._style_fields()


_NUMBER_ATTRS = {"class": "w-28 rounded border-slate-300 shadow-sm px-2 py-2 text-sm", "step": "0.01"}

VendorBillLineFormSet = modelformset_factory(
    VendorBillLine,
    fields=["quantity_billed", "rate_billed", "gst_rate"],
    widgets={
        "quantity_billed": forms.NumberInput(attrs={**_NUMBER_ATTRS, "step": "0.001"}),
        "rate_billed": forms.NumberInput(attrs=_NUMBER_ATTRS),
        "gst_rate": forms.NumberInput(attrs=_NUMBER_ATTRS),
    },
    extra=0,
)


class ReasonForm(forms.Form):
    text = forms.CharField(widget=forms.Textarea(attrs={
        "rows": 2, "class": "mt-1 w-full rounded border-slate-300 shadow-sm px-3 py-2 text-sm",
    }), required=False)


class BillRemarkForm(TailwindFormMixin, forms.ModelForm):
    class Meta:
        model = BillRemark
        fields = ["text"]
        widgets = {"text": forms.Textarea(attrs={"rows": 2})}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._style_fields()


class PaymentForm(TailwindFormMixin, forms.ModelForm):
    class Meta:
        model = Payment
        fields = ["vendor", "amount", "payment_date", "mode", "reference_number", "payment_type", "tds_amount", "remarks"]
        widgets = {
            "payment_date": forms.DateInput(attrs={"type": "date"}),
            "remarks": forms.Textarea(attrs={"rows": 2}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._style_fields()


class PaymentAllocationForm(TailwindFormMixin, forms.ModelForm):
    class Meta:
        model = PaymentAllocation
        fields = ["bill", "amount"]

    def __init__(self, *args, vendor=None, **kwargs):
        super().__init__(*args, **kwargs)
        if vendor is not None:
            self.fields["bill"].queryset = VendorBill.objects.filter(
                vendor=vendor,
                status__in=[VendorBill.Status.APPROVED_FOR_PAYMENT, VendorBill.Status.PARTIALLY_PAID],
            )
        self._style_fields()
