from django import forms

from masters.models import Item, ItemAlias, ItemCategory, RateContract, Site, Vendor, VendorDocument

TEXT_INPUT_CLASSES = "mt-1 w-full rounded border-slate-300 shadow-sm px-3 py-3 text-base"
CHECKBOX_CLASSES = "rounded border-slate-300 h-5 w-5"


class TailwindFormMixin:
    def _style_fields(self):
        for field in self.fields.values():
            widget = field.widget
            if isinstance(widget, forms.CheckboxInput):
                widget.attrs.setdefault("class", CHECKBOX_CLASSES)
            elif isinstance(widget, (forms.CheckboxSelectMultiple,)):
                continue
            else:
                widget.attrs.setdefault("class", TEXT_INPUT_CLASSES)


class SiteForm(TailwindFormMixin, forms.ModelForm):
    class Meta:
        model = Site
        fields = ["name", "code", "address", "is_factory", "active"]
        widgets = {"address": forms.Textarea(attrs={"rows": 3})}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._style_fields()


class ItemCategoryForm(TailwindFormMixin, forms.ModelForm):
    class Meta:
        model = ItemCategory
        fields = ["name", "parent"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["parent"].queryset = ItemCategory.objects.filter(parent__isnull=True)
        if self.instance.pk:
            self.fields["parent"].queryset = self.fields["parent"].queryset.exclude(pk=self.instance.pk)
        self._style_fields()


class ItemForm(TailwindFormMixin, forms.ModelForm):
    class Meta:
        model = Item
        fields = ["name", "category", "unit", "gst_rate", "hsn_code", "active"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._style_fields()


class ItemAliasForm(TailwindFormMixin, forms.ModelForm):
    class Meta:
        model = ItemAlias
        fields = ["alias_name"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._style_fields()


class VendorForm(TailwindFormMixin, forms.ModelForm):
    class Meta:
        model = Vendor
        fields = [
            "name", "gstin", "pan", "address", "state",
            "contact_person", "phone", "email",
            "bank_name", "account_number", "ifsc",
            "payment_terms_days", "categories", "tally_ledger_name", "status",
        ]
        widgets = {"address": forms.Textarea(attrs={"rows": 3})}
        labels = {
            "gstin": "GSTIN",
            "pan": "PAN",
            "ifsc": "IFSC",
            "tally_ledger_name": "Tally ledger name",
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["categories"].widget = forms.CheckboxSelectMultiple()
        self._style_fields()


class VendorDocumentForm(TailwindFormMixin, forms.ModelForm):
    class Meta:
        model = VendorDocument
        fields = ["label", "file"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._style_fields()


class RateContractForm(TailwindFormMixin, forms.ModelForm):
    class Meta:
        model = RateContract
        fields = ["item", "rate", "valid_from", "valid_to", "remarks"]
        widgets = {
            "valid_from": forms.DateInput(attrs={"type": "date"}),
            "valid_to": forms.DateInput(attrs={"type": "date"}),
            "remarks": forms.Textarea(attrs={"rows": 2}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._style_fields()
