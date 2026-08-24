from django import forms

from masters.forms import TailwindFormMixin
from purchase.models import POAttachment, PurchaseOrder, PurchaseOrderLine


class PurchaseOrderCreateForm(TailwindFormMixin, forms.ModelForm):
    class Meta:
        model = PurchaseOrder
        fields = [
            "vendor", "site", "project_name", "payment_terms_days",
            "delivery_terms", "remarks", "expected_delivery_date",
        ]
        widgets = {
            "delivery_terms": forms.Textarea(attrs={"rows": 2}),
            "remarks": forms.Textarea(attrs={"rows": 2}),
            "expected_delivery_date": forms.DateInput(attrs={"type": "date"}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._style_fields()
        self.fields["vendor"].widget.attrs["data-vendor-terms"] = "true"


class PurchaseOrderUpdateForm(TailwindFormMixin, forms.ModelForm):
    class Meta:
        model = PurchaseOrder
        fields = [
            "project_name", "payment_terms_days",
            "delivery_terms", "remarks", "expected_delivery_date",
        ]
        widgets = {
            "delivery_terms": forms.Textarea(attrs={"rows": 2}),
            "remarks": forms.Textarea(attrs={"rows": 2}),
            "expected_delivery_date": forms.DateInput(attrs={"type": "date"}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._style_fields()


class PurchaseOrderLineForm(TailwindFormMixin, forms.ModelForm):
    class Meta:
        model = PurchaseOrderLine
        fields = ["item", "description_override", "quantity", "rate", "gst_rate"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["gst_rate"].required = False
        self._style_fields()


class POAttachmentForm(TailwindFormMixin, forms.ModelForm):
    class Meta:
        model = POAttachment
        fields = ["label", "file"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._style_fields()


class ReasonForm(forms.Form):
    """Generic single-textarea form for approve/reject comments and cancel
    reasons. Kept optional here — cancellation still requires a non-empty
    reason, but that's enforced server-side by PurchaseOrder.cancel(), since
    approve/reject share this same field and their comment is optional."""
    text = forms.CharField(widget=forms.Textarea(attrs={"rows": 2, "class": "mt-1 w-full rounded border-slate-300 shadow-sm px-3 py-2 text-sm"}), required=False)


class SendForm(TailwindFormMixin, forms.Form):
    channel = forms.ChoiceField(choices=PurchaseOrder.SentVia.choices)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._style_fields()
