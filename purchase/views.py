from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.core.exceptions import PermissionDenied
from django.db.models import Q
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.template.loader import render_to_string
from django.urls import reverse
from django.views.generic import CreateView, DetailView, ListView, UpdateView, View

from accounts_stub import roles
from masters.models import Item, RateContract, Site, Vendor
from purchase.forms import (
    POAttachmentForm,
    PurchaseOrderCreateForm,
    PurchaseOrderLineForm,
    PurchaseOrderUpdateForm,
    ReasonForm,
    SendForm,
)
from purchase.models import (
    ApprovalRule,
    InvalidStatusTransition,
    POAttachment,
    PurchaseOrder,
    PurchaseOrderLine,
)
from purchase.permissions import (
    POManageRequiredMixin,
    POViewRequiredMixin,
    VendorLedgerViewRequiredMixin,
    visible_po_queryset,
)

PAGE_SIZE = 20


def _lines_section_response(request, po):
    html = render_to_string(
        "purchase/_po_lines_section.html",
        {"po": po, "line_form": PurchaseOrderLineForm(), "can_manage_po": roles.can_manage_purchase_orders(request.user)},
        request=request,
    )
    return HttpResponse(html)


class PurchaseOrderListView(POViewRequiredMixin, ListView):
    model = PurchaseOrder
    template_name = "purchase/po_list.html"
    context_object_name = "purchase_orders"
    paginate_by = PAGE_SIZE

    def get_queryset(self):
        qs = visible_po_queryset(self.request.user, PurchaseOrder.objects.select_related("vendor", "site"))
        q = self.request.GET.get("q", "").strip()
        if q:
            qs = qs.filter(Q(po_number__icontains=q) | Q(vendor__name__icontains=q))
        status = self.request.GET.get("status", "")
        if status:
            qs = qs.filter(status=status)
        site_id = self.request.GET.get("site", "")
        if site_id:
            qs = qs.filter(site_id=site_id)
        return qs

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["q"] = self.request.GET.get("q", "")
        ctx["status"] = self.request.GET.get("status", "")
        ctx["site"] = self.request.GET.get("site", "")
        ctx["status_choices"] = PurchaseOrder.Status.choices
        ctx["sites"] = Site.objects.all()
        return ctx


class PurchaseOrderCreateView(POManageRequiredMixin, CreateView):
    model = PurchaseOrder
    form_class = PurchaseOrderCreateForm
    template_name = "purchase/po_form.html"

    def get_initial(self):
        initial = super().get_initial()
        vendor_id = self.request.GET.get("vendor")
        if vendor_id:
            vendor = Vendor.objects.filter(pk=vendor_id).first()
            if vendor:
                initial["vendor"] = vendor
                initial["payment_terms_days"] = vendor.payment_terms_days
        return initial

    def form_valid(self, form):
        form.instance.created_by = self.request.user
        response = super().form_valid(form)
        messages.success(self.request, f"Draft PO {self.object.po_number} created. Add line items to submit for approval.")
        return response

    def get_success_url(self):
        return reverse("purchase:po_detail", args=[self.object.pk])

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["vendor_terms_map"] = {v.pk: v.payment_terms_days for v in Vendor.objects.all()}
        return ctx


class PurchaseOrderDetailView(POViewRequiredMixin, DetailView):
    model = PurchaseOrder
    template_name = "purchase/po_detail.html"
    context_object_name = "po"

    def get_queryset(self):
        return visible_po_queryset(self.request.user, PurchaseOrder.objects.select_related("vendor", "site"))

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        po = self.object
        user = self.request.user
        ctx["line_form"] = PurchaseOrderLineForm()
        ctx["attachment_form"] = POAttachmentForm()
        ctx["reason_form"] = ReasonForm()
        ctx["send_form"] = SendForm()
        ctx["can_manage_po"] = roles.can_manage_purchase_orders(user)
        ctx["can_approve"] = (
            po.status == PurchaseOrder.Status.PENDING_APPROVAL
            and ApprovalRule.can_user_approve(user, po.grand_total)
        )
        ctx["approval_actions"] = po.approval_actions.select_related("actor")
        return ctx


class PurchaseOrderUpdateView(POManageRequiredMixin, UpdateView):
    model = PurchaseOrder
    form_class = PurchaseOrderUpdateForm
    template_name = "purchase/po_form.html"

    def get_object(self, queryset=None):
        po = super().get_object(queryset)
        if not po.is_editable:
            raise PermissionDenied("This PO can no longer be edited directly; use Amend instead.")
        return po

    def get_success_url(self):
        messages.success(self.request, "Purchase order updated.")
        return reverse("purchase:po_detail", args=[self.object.pk])


class ItemSearchView(POManageRequiredMixin, View):
    def get(self, request):
        q = request.GET.get("q", "").strip()
        vendor_id = request.GET.get("vendor")
        vendor = Vendor.objects.filter(pk=vendor_id).first() if vendor_id else None

        items = []
        if q:
            items = list(
                Item.objects.filter(active=True).filter(
                    Q(name__icontains=q) | Q(code__icontains=q) | Q(aliases__alias_name__icontains=q)
                ).distinct()[:10]
            )

        results = []
        for item in items:
            contract_rate = RateContract.current_rate(vendor, item) if vendor else None
            results.append({
                "item": item,
                "rate": contract_rate if contract_rate is not None else "",
            })
        return render(request, "purchase/_item_search_results.html", {"results": results})


class POLineCreateView(POManageRequiredMixin, View):
    def post(self, request, pk):
        po = get_object_or_404(PurchaseOrder, pk=pk)
        if not po.is_editable:
            messages.error(request, "This PO is no longer editable.")
            return _lines_section_response(request, po)
        form = PurchaseOrderLineForm(request.POST)
        if form.is_valid():
            line = form.save(commit=False)
            line.po = po
            line.created_by = request.user
            line.save()
        else:
            messages.error(request, "Could not add line: " + "; ".join(
                f"{field}: {', '.join(errs)}" for field, errs in form.errors.items()
            ))
        po.refresh_from_db()
        return _lines_section_response(request, po)


class POLineUpdateView(POManageRequiredMixin, View):
    def post(self, request, pk, line_pk):
        po = get_object_or_404(PurchaseOrder, pk=pk)
        line = get_object_or_404(PurchaseOrderLine, pk=line_pk, po=po)
        if not po.is_editable:
            messages.error(request, "This PO is no longer editable.")
            return _lines_section_response(request, po)
        form = PurchaseOrderLineForm(request.POST, instance=line)
        if form.is_valid():
            form.save()
        else:
            messages.error(request, "Could not update line: " + "; ".join(
                f"{field}: {', '.join(errs)}" for field, errs in form.errors.items()
            ))
        po.refresh_from_db()
        return _lines_section_response(request, po)


class POLineDeleteView(POManageRequiredMixin, View):
    def post(self, request, pk, line_pk):
        po = get_object_or_404(PurchaseOrder, pk=pk)
        line = get_object_or_404(PurchaseOrderLine, pk=line_pk, po=po)
        if not po.is_editable:
            messages.error(request, "This PO is no longer editable.")
            return _lines_section_response(request, po)
        line.delete()
        po.refresh_from_db()
        return _lines_section_response(request, po)


class POSubmitView(POManageRequiredMixin, View):
    def post(self, request, pk):
        po = get_object_or_404(PurchaseOrder, pk=pk)
        try:
            po.submit_for_approval(request.user)
            messages.success(request, f"{po.po_number} submitted for approval.")
        except InvalidStatusTransition as exc:
            messages.error(request, str(exc))
        return redirect("purchase:po_detail", pk=pk)


class POApproveView(LoginRequiredMixin, View):
    def post(self, request, pk):
        po = get_object_or_404(PurchaseOrder, pk=pk)
        if not ApprovalRule.can_user_approve(request.user, po.grand_total):
            raise PermissionDenied("You are not authorized to approve this PO.")
        comment = request.POST.get("text", "")
        try:
            po.approve(request.user, comment=comment)
            messages.success(request, f"{po.po_number} approved.")
        except InvalidStatusTransition as exc:
            messages.error(request, str(exc))
        return redirect("purchase:po_detail", pk=pk)


class PORejectView(LoginRequiredMixin, View):
    def post(self, request, pk):
        po = get_object_or_404(PurchaseOrder, pk=pk)
        if not ApprovalRule.can_user_approve(request.user, po.grand_total):
            raise PermissionDenied("You are not authorized to act on this PO.")
        comment = request.POST.get("text", "")
        try:
            po.reject(request.user, comment=comment)
            messages.success(request, f"{po.po_number} sent back to draft.")
        except InvalidStatusTransition as exc:
            messages.error(request, str(exc))
        return redirect("purchase:po_detail", pk=pk)


class POMarkSentView(POManageRequiredMixin, View):
    def post(self, request, pk):
        po = get_object_or_404(PurchaseOrder, pk=pk)
        channel = request.POST.get("channel", "")
        try:
            po.mark_sent(request.user, channel)
            messages.success(request, f"{po.po_number} marked as sent.")
        except InvalidStatusTransition as exc:
            messages.error(request, str(exc))
        return redirect("purchase:po_detail", pk=pk)


class POCancelView(POManageRequiredMixin, View):
    def post(self, request, pk):
        po = get_object_or_404(PurchaseOrder, pk=pk)
        reason = request.POST.get("text", "")
        try:
            po.cancel(request.user, reason)
            messages.success(request, f"{po.po_number} cancelled.")
        except InvalidStatusTransition as exc:
            messages.error(request, str(exc))
        return redirect("purchase:po_detail", pk=pk)


class POCloseView(POManageRequiredMixin, View):
    def post(self, request, pk):
        po = get_object_or_404(PurchaseOrder, pk=pk)
        try:
            po.close(request.user)
            messages.success(request, f"{po.po_number} closed.")
        except InvalidStatusTransition as exc:
            messages.error(request, str(exc))
        return redirect("purchase:po_detail", pk=pk)


class POAmendView(POManageRequiredMixin, View):
    def post(self, request, pk):
        po = get_object_or_404(PurchaseOrder, pk=pk)
        try:
            po.amend(request.user)
            messages.success(request, f"{po.po_number} reopened as revision {po.revision_number} for editing.")
        except InvalidStatusTransition as exc:
            messages.error(request, str(exc))
        return redirect("purchase:po_detail", pk=pk)


class POAttachmentCreateView(POManageRequiredMixin, View):
    def post(self, request, pk):
        po = get_object_or_404(PurchaseOrder, pk=pk)
        form = POAttachmentForm(request.POST, request.FILES)
        if form.is_valid():
            attachment = form.save(commit=False)
            attachment.po = po
            attachment.created_by = request.user
            attachment.save()
            messages.success(request, "Attachment uploaded.")
        else:
            messages.error(request, "Could not upload attachment.")
        return redirect("purchase:po_detail", pk=pk)


class POAttachmentDeleteView(POManageRequiredMixin, View):
    def post(self, request, pk, att_pk):
        attachment = get_object_or_404(POAttachment, pk=att_pk, po_id=pk)
        attachment.delete()
        messages.success(request, "Attachment removed.")
        return redirect("purchase:po_detail", pk=pk)


class POPdfView(POViewRequiredMixin, View):
    def get(self, request, pk):
        from django.conf import settings as django_settings
        from weasyprint import HTML

        from purchase.utils import amount_in_words, gst_breakup

        po = get_object_or_404(
            visible_po_queryset(request.user, PurchaseOrder.objects.select_related("vendor", "site")),
            pk=pk,
        )
        cgst, sgst, igst = gst_breakup(po.vendor.state, django_settings.COMPANY_STATE, po.gst_amount)
        html_string = render_to_string("purchase/po_pdf.html", {
            "po": po,
            "lines": po.lines.select_related("item"),
            "company": {
                "name": django_settings.COMPANY_NAME,
                "address": django_settings.COMPANY_ADDRESS,
                "gstin": django_settings.COMPANY_GSTIN,
                "state": django_settings.COMPANY_STATE,
            },
            "cgst": cgst, "sgst": sgst, "igst": igst,
            "amount_in_words": amount_in_words(po.grand_total),
        })
        pdf_bytes = HTML(string=html_string, base_url=request.build_absolute_uri("/")).write_pdf()
        response = HttpResponse(pdf_bytes, content_type="application/pdf")
        response["Content-Disposition"] = f'inline; filename="{po.po_number.replace("/", "-")}.pdf"'
        return response


class VendorLedgerListView(VendorLedgerViewRequiredMixin, ListView):
    model = Vendor
    template_name = "purchase/vendor_ledger_list.html"
    context_object_name = "vendors"
    paginate_by = PAGE_SIZE

    def get_queryset(self):
        qs = Vendor.objects.all()
        q = self.request.GET.get("q", "").strip()
        if q:
            qs = qs.filter(Q(name__icontains=q) | Q(code__icontains=q))
        return qs

    def get_context_data(self, **kwargs):
        from purchase.services import vendor_ledger

        ctx = super().get_context_data(**kwargs)
        ctx["q"] = self.request.GET.get("q", "")
        ctx["vendor_rows"] = [
            {"vendor": v, "open_value": vendor_ledger(v)["open_value"]} for v in ctx["vendors"]
        ]
        return ctx


class VendorLedgerView(VendorLedgerViewRequiredMixin, DetailView):
    model = Vendor
    template_name = "purchase/vendor_ledger.html"
    context_object_name = "vendor"

    def get_context_data(self, **kwargs):
        from purchase.services import vendor_ledger

        ctx = super().get_context_data(**kwargs)
        ledger = vendor_ledger(self.object)
        ctx["purchase_orders"] = visible_po_queryset(self.request.user, ledger["purchase_orders"])
        ctx["open_value"] = ledger["open_value"]
        return ctx


class ProcurementLeadTimeView(POViewRequiredMixin, View):
    template_name = "purchase/lead_time_report.html"

    def get(self, request):
        from purchase.services import procurement_lead_time
        data = procurement_lead_time()
        return render(request, self.template_name, data)
