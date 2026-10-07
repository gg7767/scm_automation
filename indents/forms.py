from decimal import Decimal
from django import forms
from django.db.models import Q

from indents.models import Indent, IndentLine
from masters.forms import TailwindFormMixin
from masters.models import Item, ItemCategory


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


class QuickIndentLineForm(TailwindFormMixin, forms.Form):
    """Quick indent line form for site members: choose existing item or create new."""
    
    ITEM_CHOICE = [('existing', 'Select from Inventory'), ('new', 'Add New Item')]
    
    item_type = forms.ChoiceField(
        choices=ITEM_CHOICE,
        widget=forms.RadioSelect,
        initial='existing',
        label="Item"
    )
    
    # For existing items
    existing_item = forms.ModelChoiceField(
        queryset=Item.objects.filter(active=True).order_by('name'),
        required=False,
        label="Select Item",
        help_text="Start typing to search"
    )
    
    # For new items
    new_item_name = forms.CharField(
        max_length=255,
        required=False,
        label="Item Name",
        widget=forms.TextInput(attrs={"placeholder": "e.g., Cement 53 Grade"})
    )
    new_item_category = forms.ModelChoiceField(
        queryset=ItemCategory.objects.filter(parent__isnull=False).order_by('name'),
        required=False,
        label="Category",
        help_text="Select the item category"
    )
    new_item_unit = forms.ChoiceField(
        choices=Item.Unit.choices,
        required=False,
        label="Unit"
    )
    new_item_gst_rate = forms.DecimalField(
        max_digits=5,
        decimal_places=2,
        required=False,
        initial=Decimal("18.00"),
        label="GST Rate (%)"
    )
    
    # Quantity (required for both flows)
    quantity = forms.DecimalField(
        max_digits=12,
        decimal_places=3,
        min_value=Decimal("0.001"),
        label="Quantity Required",
        help_text="Enter the quantity needed"
    )
    
    purpose = forms.CharField(
        max_length=255,
        required=False,
        label="Purpose/Notes",
        widget=forms.TextInput(attrs={"placeholder": "Why this item is needed"})
    )
    
    def clean(self):
        cleaned_data = super().clean()
        item_type = cleaned_data.get("item_type")
        
        if item_type == 'existing':
            if not cleaned_data.get("existing_item"):
                raise forms.ValidationError("Please select an item from inventory.")
        
        elif item_type == 'new':
            if not cleaned_data.get("new_item_name"):
                raise forms.ValidationError("Item name is required for new items.")
            if not cleaned_data.get("new_item_category"):
                raise forms.ValidationError("Item category is required.")
            if not cleaned_data.get("new_item_unit"):
                raise forms.ValidationError("Unit is required.")
        
        if not cleaned_data.get("quantity"):
            raise forms.ValidationError("Quantity is required.")
        
        return cleaned_data
    
    def save_as_indent_line(self, indent):
        """Create or select item, then create IndentLine."""
        cleaned_data = self.cleaned_data
        item_type = cleaned_data["item_type"]
        
        # Get or create the item
        if item_type == 'existing':
            item = cleaned_data["existing_item"]
        else:
            # Create new item
            item, created = Item.objects.get_or_create(
                name=cleaned_data["new_item_name"],
                defaults={
                    "category": cleaned_data["new_item_category"],
                    "unit": cleaned_data["new_item_unit"],
                    "gst_rate": cleaned_data["new_item_gst_rate"],
                    "active": True,
                }
            )
        
        # Create the indent line
        line = IndentLine.objects.create(
            indent=indent,
            item=item,
            quantity=cleaned_data["quantity"],
            unit=item.unit,
            purpose=cleaned_data.get("purpose", "")
        )
        return line


class IndentLineForm(TailwindFormMixin, forms.ModelForm):
    """Traditional form for editing existing lines."""
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
