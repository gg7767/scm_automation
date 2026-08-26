from django import forms

from logistics.models import TransportTrip
from masters.forms import TailwindFormMixin


class TransportTripForm(TailwindFormMixin, forms.ModelForm):
    class Meta:
        model = TransportTrip
        fields = [
            "trip_date", "vehicle_number", "transporter", "driver_name", "driver_phone",
            "from_location", "to_site", "linked_po", "element_description",
            "freight_amount", "billable_to", "remarks", "pod_photo",
        ]
        widgets = {"trip_date": forms.DateInput(attrs={"type": "date"}), "remarks": forms.Textarea(attrs={"rows": 2})}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        from masters.models import Vendor
        self.fields["transporter"].queryset = Vendor.objects.filter(categories__name="Transport").distinct()
        self._style_fields()
