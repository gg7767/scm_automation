from django import forms

from assets.models import Machine, MachineDeployment, MachineLog
from masters.forms import TailwindFormMixin


class MachineForm(TailwindFormMixin, forms.ModelForm):
    class Meta:
        model = Machine
        fields = [
            "name", "category", "ownership", "hire_vendor", "hire_rate", "rate_unit",
            "purchase_date", "purchase_value", "status",
        ]
        widgets = {"purchase_date": forms.DateInput(attrs={"type": "date"})}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._style_fields()


class MachineDeploymentForm(TailwindFormMixin, forms.ModelForm):
    class Meta:
        model = MachineDeployment
        fields = ["site", "from_date", "remarks"]
        widgets = {"from_date": forms.DateInput(attrs={"type": "date"}), "remarks": forms.Textarea(attrs={"rows": 2})}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._style_fields()


class MachineLogForm(TailwindFormMixin, forms.ModelForm):
    class Meta:
        model = MachineLog
        fields = ["log_date", "hours_run", "km", "fuel_litres", "operator_name", "remarks"]
        widgets = {"log_date": forms.DateInput(attrs={"type": "date"}), "remarks": forms.Textarea(attrs={"rows": 2})}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._style_fields()
