from django import forms

from indents.models import Indent, IndentLine
from masters.forms import TailwindFormMixin


class IndentForm(TailwindFormMixin, forms.ModelForm):
    class Meta:
        model = Indent
        fields = ["site", "project_name", "required_by_date", "priority", "remarks"]
        widgets = {
            "required_by_date": forms.DateInput(attrs={"type": "date"}),
            "remarks": forms.Textarea(attrs={"rows": 2}),
        }

    def __init__(self, *args, restrict_site=None, **kwargs):
        super().__init__(*args, **kwargs)
        if restrict_site is not None:
            self.fields["site"].queryset = self.fields["site"].queryset.filter(pk=restrict_site.pk)
            self.fields["site"].initial = restrict_site
            self.fields["site"].widget = forms.HiddenInput()
        self._style_fields()


class IndentLineForm(TailwindFormMixin, forms.ModelForm):
    class Meta:
        model = IndentLine
        fields = ["item", "quantity", "purpose"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._style_fields()


class ReasonForm(forms.Form):
    text = forms.CharField(widget=forms.Textarea(attrs={
        "rows": 2, "class": "mt-1 w-full rounded border-slate-300 shadow-sm px-3 py-2 text-sm",
    }), required=False)
