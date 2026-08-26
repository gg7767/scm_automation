from django.contrib import messages
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.generic import DetailView, ListView, View

from accounts_stub import roles
from purchase.models import PurchaseOrder
from stores.forms import GRNForm, GRNLineFormSet
from stores.models import GRN, InvalidStatusTransition
from stores.permissions import (
    GRNAccessRequiredMixin,
    GRNReversalRequiredMixin,
    GRNViewRequiredMixin,
    sites_for_user,
    visible_grn_queryset,
)
from stores.services import populate_lines_from_po

PAGE_SIZE = 20


class GRNListView(GRNViewRequiredMixin, ListView):
    model = GRN
    template_name = "stores/grn_list.html"
    context_object_name = "grns"
    paginate_by = PAGE_SIZE

    def get_queryset(self):
        qs = visible_grn_queryset(self.request.user, GRN.objects.select_related("po", "site"))
        status = self.request.GET.get("status", "")
        if status:
            qs = qs.filter(status=status)
        return qs

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["status"] = self.request.GET.get("status", "")
        ctx["status_choices"] = GRN.Status.choices
        return ctx


class OpenPOPickerView(GRNAccessRequiredMixin, ListView):
    """Step 1 of GRN entry: pick which open PO a delivery is against."""
    model = PurchaseOrder
    template_name = "stores/po_picker.html"
    context_object_name = "purchase_orders"

    def get_queryset(self):
        sites = sites_for_user(self.request.user)
        return PurchaseOrder.objects.filter(
            site__in=sites,
            status__in=[PurchaseOrder.Status.SENT, PurchaseOrder.Status.PARTIALLY_DELIVERED],
        ).select_related("vendor", "site")


class GRNCreateView(GRNAccessRequiredMixin, View):
    template_name = "stores/grn_form.html"
    is_reversal = False

    def get(self, request):
        po_id = request.GET.get("po")
        po = get_object_or_404(PurchaseOrder, pk=po_id)
        if roles.is_site_restricted(request.user) and po.site != roles.user_site(request.user):
            messages.error(request, "You can only log deliveries for your own site.")
            return redirect("stores:po_picker")
        form = GRNForm()
        return render(request, self.template_name, {"form": form, "po": po, "is_reversal": self.is_reversal})

    def post(self, request):
        po_id = request.GET.get("po") or request.POST.get("po")
        po = get_object_or_404(PurchaseOrder, pk=po_id)
        form = GRNForm(request.POST, request.FILES)
        if form.is_valid():
            grn = form.save(commit=False)
            grn.po = po
            grn.received_by = request.user
            grn.created_by = request.user
            grn.is_reversal = self.is_reversal
            if self.is_reversal:
                original_id = request.POST.get("reverses")
                grn.reverses = get_object_or_404(GRN, pk=original_id) if original_id else None
            grn.save()
            populate_lines_from_po(grn)
            messages.success(request, f"Draft {'reversal ' if self.is_reversal else ''}GRN {grn.grn_number} created.")
            return redirect("stores:grn_detail", pk=grn.pk)
        return render(request, self.template_name, {"form": form, "po": po, "is_reversal": self.is_reversal})


class GRNReversalCreateView(GRNReversalRequiredMixin, GRNCreateView):
    is_reversal = True


class GRNDetailView(GRNViewRequiredMixin, DetailView):
    model = GRN
    template_name = "stores/grn_detail.html"
    context_object_name = "grn"

    def get_queryset(self):
        return visible_grn_queryset(self.request.user, GRN.objects.select_related("po", "site"))

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["formset"] = GRNLineFormSet(queryset=self.object.lines.select_related("po_line__item"))
        return ctx


class GRNLinesUpdateView(GRNAccessRequiredMixin, View):
    def post(self, request, pk):
        grn = get_object_or_404(GRN, pk=pk)
        if not grn.is_editable:
            messages.error(request, "This GRN has already been submitted and is immutable.")
            return redirect("stores:grn_detail", pk=pk)
        formset = GRNLineFormSet(request.POST, queryset=grn.lines.select_related("po_line__item"))
        if formset.is_valid():
            formset.save()
            messages.success(request, "Quantities updated.")
        else:
            messages.error(request, "Could not save: please check the entered quantities.")
        return redirect("stores:grn_detail", pk=pk)


class GRNSubmitView(GRNAccessRequiredMixin, View):
    def post(self, request, pk):
        grn = get_object_or_404(GRN, pk=pk)
        try:
            grn.submit(request.user)
            messages.success(request, f"{grn.grn_number} submitted.")
        except InvalidStatusTransition as exc:
            messages.error(request, str(exc))
        return redirect("stores:grn_detail", pk=pk)
