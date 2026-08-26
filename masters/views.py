from django.contrib import messages
from django.core.exceptions import ValidationError
from django.db.models import Q
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse, reverse_lazy
from django.views.generic import CreateView, DetailView, ListView, UpdateView, View

from masters.forms import (
    ItemAliasForm,
    ItemCategoryForm,
    ItemForm,
    RateContractForm,
    SiteForm,
    VendorDocumentForm,
    VendorForm,
)
from masters.models import Item, ItemAlias, ItemCategory, RateContract, Site, Vendor, VendorDocument
from masters.permissions import MastersManagerRequiredMixin

PAGE_SIZE = 20


class CreatedByMixin:
    def form_valid(self, form):
        if not form.instance.pk:
            form.instance.created_by = self.request.user
        response = super().form_valid(form)
        messages.success(self.request, f"{self.model._meta.verbose_name.title()} saved.")
        return response


# --- Site -------------------------------------------------------------

class SiteListView(MastersManagerRequiredMixin, ListView):
    model = Site
    template_name = "masters/site_list.html"
    context_object_name = "sites"
    paginate_by = PAGE_SIZE

    def get_queryset(self):
        qs = Site.objects.all()
        q = self.request.GET.get("q", "").strip()
        if q:
            qs = qs.filter(Q(name__icontains=q) | Q(code__icontains=q))
        status = self.request.GET.get("status", "")
        if status == "active":
            qs = qs.filter(active=True)
        elif status == "inactive":
            qs = qs.filter(active=False)
        return qs

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["q"] = self.request.GET.get("q", "")
        ctx["status"] = self.request.GET.get("status", "")
        return ctx


class SiteCreateView(MastersManagerRequiredMixin, CreatedByMixin, CreateView):
    model = Site
    form_class = SiteForm
    template_name = "masters/site_form.html"
    success_url = reverse_lazy("masters:site_list")


class SiteUpdateView(MastersManagerRequiredMixin, CreatedByMixin, UpdateView):
    model = Site
    form_class = SiteForm
    template_name = "masters/site_form.html"
    success_url = reverse_lazy("masters:site_list")


# --- ItemCategory -------------------------------------------------------

class ItemCategoryListView(MastersManagerRequiredMixin, ListView):
    model = ItemCategory
    template_name = "masters/category_list.html"
    context_object_name = "categories"
    paginate_by = PAGE_SIZE

    def get_queryset(self):
        qs = ItemCategory.objects.select_related("parent")
        q = self.request.GET.get("q", "").strip()
        if q:
            qs = qs.filter(name__icontains=q)
        return qs

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["q"] = self.request.GET.get("q", "")
        return ctx


class ItemCategoryCreateView(MastersManagerRequiredMixin, CreatedByMixin, CreateView):
    model = ItemCategory
    form_class = ItemCategoryForm
    template_name = "masters/category_form.html"
    success_url = reverse_lazy("masters:category_list")


class ItemCategoryUpdateView(MastersManagerRequiredMixin, CreatedByMixin, UpdateView):
    model = ItemCategory
    form_class = ItemCategoryForm
    template_name = "masters/category_form.html"
    success_url = reverse_lazy("masters:category_list")


# --- Item -----------------------------------------------------------------

class ItemListView(MastersManagerRequiredMixin, ListView):
    model = Item
    template_name = "masters/item_list.html"
    context_object_name = "items"
    paginate_by = PAGE_SIZE

    def get_queryset(self):
        qs = Item.objects.select_related("category")
        q = self.request.GET.get("q", "").strip()
        if q:
            qs = qs.filter(
                Q(name__icontains=q) | Q(code__icontains=q)
                | Q(hsn_code__icontains=q) | Q(aliases__alias_name__icontains=q)
            ).distinct()
        category_id = self.request.GET.get("category", "")
        if category_id:
            qs = qs.filter(category_id=category_id)
        status = self.request.GET.get("status", "")
        if status == "active":
            qs = qs.filter(active=True)
        elif status == "inactive":
            qs = qs.filter(active=False)
        return qs

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["q"] = self.request.GET.get("q", "")
        ctx["category"] = self.request.GET.get("category", "")
        ctx["status"] = self.request.GET.get("status", "")
        ctx["categories"] = ItemCategory.objects.all()
        return ctx


class ItemDetailView(MastersManagerRequiredMixin, DetailView):
    model = Item
    template_name = "masters/item_detail.html"
    context_object_name = "item"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["alias_form"] = ItemAliasForm()
        return ctx


class ItemCreateView(MastersManagerRequiredMixin, CreatedByMixin, CreateView):
    model = Item
    form_class = ItemForm
    template_name = "masters/item_form.html"

    def get_success_url(self):
        return reverse("masters:item_detail", args=[self.object.pk])


class ItemUpdateView(MastersManagerRequiredMixin, CreatedByMixin, UpdateView):
    model = Item
    form_class = ItemForm
    template_name = "masters/item_form.html"

    def get_success_url(self):
        return reverse("masters:item_detail", args=[self.object.pk])


class ItemAliasCreateView(MastersManagerRequiredMixin, View):
    def post(self, request, pk):
        item = get_object_or_404(Item, pk=pk)
        form = ItemAliasForm(request.POST)
        if form.is_valid():
            alias = form.save(commit=False)
            alias.item = item
            alias.created_by = request.user
            alias.save()
            messages.success(request, "Alias added.")
        else:
            messages.error(request, "Could not add alias: " + "; ".join(
                f"{field}: {', '.join(errs)}" for field, errs in form.errors.items()
            ))
        return redirect("masters:item_detail", pk=item.pk)


class ItemAliasDeleteView(MastersManagerRequiredMixin, View):
    def post(self, request, pk, alias_pk):
        alias = get_object_or_404(ItemAlias, pk=alias_pk, item_id=pk)
        alias.delete()
        messages.success(request, "Alias removed.")
        return redirect("masters:item_detail", pk=pk)


# --- Vendor -----------------------------------------------------------------

class VendorListView(MastersManagerRequiredMixin, ListView):
    model = Vendor
    template_name = "masters/vendor_list.html"
    context_object_name = "vendors"
    paginate_by = PAGE_SIZE

    def get_queryset(self):
        qs = Vendor.objects.all()
        q = self.request.GET.get("q", "").strip()
        if q:
            qs = qs.filter(
                Q(name__icontains=q) | Q(code__icontains=q)
                | Q(gstin__icontains=q) | Q(pan__icontains=q)
            )
        status = self.request.GET.get("status", "")
        if status:
            qs = qs.filter(status=status)
        return qs

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["q"] = self.request.GET.get("q", "")
        ctx["status"] = self.request.GET.get("status", "")
        ctx["status_choices"] = Vendor.Status.choices
        return ctx


class VendorDetailView(MastersManagerRequiredMixin, DetailView):
    model = Vendor
    template_name = "masters/vendor_detail.html"
    context_object_name = "vendor"

    def get_context_data(self, **kwargs):
        from masters.services import vendor_performance_score

        ctx = super().get_context_data(**kwargs)
        ctx["document_form"] = VendorDocumentForm()
        ctx["rate_contract_form"] = RateContractForm()
        ctx["performance"] = vendor_performance_score(self.object)
        return ctx


class VendorCreateView(MastersManagerRequiredMixin, CreatedByMixin, CreateView):
    model = Vendor
    form_class = VendorForm
    template_name = "masters/vendor_form.html"

    def get_success_url(self):
        return reverse("masters:vendor_detail", args=[self.object.pk])


class VendorUpdateView(MastersManagerRequiredMixin, CreatedByMixin, UpdateView):
    model = Vendor
    form_class = VendorForm
    template_name = "masters/vendor_form.html"

    def get_success_url(self):
        return reverse("masters:vendor_detail", args=[self.object.pk])


class VendorDocumentCreateView(MastersManagerRequiredMixin, View):
    def post(self, request, pk):
        vendor = get_object_or_404(Vendor, pk=pk)
        form = VendorDocumentForm(request.POST, request.FILES)
        if form.is_valid():
            doc = form.save(commit=False)
            doc.vendor = vendor
            doc.created_by = request.user
            doc.save()
            messages.success(request, "Document uploaded.")
        else:
            messages.error(request, "Could not upload document: " + "; ".join(
                f"{field}: {', '.join(errs)}" for field, errs in form.errors.items()
            ))
        return redirect("masters:vendor_detail", pk=vendor.pk)


class VendorDocumentDeleteView(MastersManagerRequiredMixin, View):
    def post(self, request, pk, doc_pk):
        doc = get_object_or_404(VendorDocument, pk=doc_pk, vendor_id=pk)
        doc.delete()
        messages.success(request, "Document removed.")
        return redirect("masters:vendor_detail", pk=pk)


class RateContractCreateView(MastersManagerRequiredMixin, View):
    def post(self, request, pk):
        vendor = get_object_or_404(Vendor, pk=pk)
        form = RateContractForm(request.POST)
        if form.is_valid():
            contract = form.save(commit=False)
            contract.vendor = vendor
            contract.created_by = request.user
            try:
                contract.full_clean()
            except ValidationError as exc:
                messages.error(request, "Could not add rate contract: " + "; ".join(exc.messages))
                return redirect("masters:vendor_detail", pk=vendor.pk)
            contract.save()
            messages.success(request, "Rate contract added.")
        else:
            messages.error(request, "Could not add rate contract: " + "; ".join(
                f"{field}: {', '.join(errs)}" for field, errs in form.errors.items()
            ))
        return redirect("masters:vendor_detail", pk=vendor.pk)


class RateContractDeleteView(MastersManagerRequiredMixin, View):
    def post(self, request, pk, contract_pk):
        contract = get_object_or_404(RateContract, pk=contract_pk, vendor_id=pk)
        contract.delete()
        messages.success(request, "Rate contract removed.")
        return redirect("masters:vendor_detail", pk=pk)
