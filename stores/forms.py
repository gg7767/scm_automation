from django import forms
from django.forms import modelformset_factory

from masters.forms import TailwindFormMixin
from stores.models import GRN, GRNLine


class GRNForm(TailwindFormMixin, forms.ModelForm):
    class Meta:
        model = GRN
        fields = ["vehicle_number", "challan_number", "challan_date", "challan_photo", "remarks"]
        widgets = {
            "challan_date": forms.DateInput(attrs={"type": "date"}),
            "remarks": forms.Textarea(attrs={"rows": 2}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._style_fields()


_NUMBER_ATTRS = {"class": "w-24 rounded border-slate-300 shadow-sm px-2 py-2 text-sm", "step": "0.001"}

GRNLineFormSet = modelformset_factory(
    GRNLine,
    fields=["qty_received", "qty_accepted", "qty_rejected", "rejection_reason"],
    widgets={
        "qty_received": forms.NumberInput(attrs=_NUMBER_ATTRS),
        "qty_accepted": forms.NumberInput(attrs=_NUMBER_ATTRS),
        "qty_rejected": forms.NumberInput(attrs=_NUMBER_ATTRS),
        "rejection_reason": forms.TextInput(attrs={"class": "w-full rounded border-slate-300 shadow-sm px-2 py-2 text-sm"}),
    },
    extra=0,
)
