from decimal import Decimal

from accounts_stub import roles
from django.contrib import messages
from django.core.exceptions import PermissionDenied
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.generic import CreateView, DetailView, ListView, View

from billing.forms import (
    BillRemarkForm,
    PaymentAllocationForm,
    PaymentForm,
    ReasonForm,
    VendorBillForm,
    VendorBillLineFormSet,
)
from billing.models import DebitNote, InvalidStatusTransition, Payment, PaymentAllocation, VendorBill, VendorBillLine
from billing.permissions import BillAccessRequiredMixin, MismatchOverrideRequiredMixin
from masters.models import Vendor
from purchase.models import PurchaseOrder
from stores.models import DebitNoteCandidate

PAGE_SIZE = 20


class BillablePOPickerView(BillAccessRequiredMixin, ListView):
    model = PurchaseOrder
    template_name = "billing/po_picker.html"
    context_object_name = "purchase_orders"

    def get_queryset(self):
        return PurchaseOrder.objects.filter(
            status__in=[PurchaseOrder.Status.SENT, PurchaseOrder.Status.PARTIALLY_DELIVERED],
        ).select_related("vendor", "site")


class VendorBillListView(BillAccessRequiredMixin, ListView):
    model = VendorBill
    template_name = "billing/bill_list.html"
    context_object_name = "bills"
    paginate_by = PAGE_SIZE

    def get_queryset(self):
        qs = VendorBill.objects.select_related("vendor", "po")
        status = self.request.GET.get("status", "")
        if status:
            qs = qs.filter(status=status)
        q = self.request.GET.get("q", "").strip()
        if q:
            qs = qs.filter(bill_number__icontains=q) | qs.filter(vendor_invoice_number__icontains=q)
        return qs

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["status"] = self.request.GET.get("status", "")
        ctx["q"] = self.request.GET.get("q", "")
        ctx["status_choices"] = VendorBill.Status.choices
        return ctx


class VendorBillCreateView(BillAccessRequiredMixin, View):
    template_name = "billing/bill_form.html"

    def get(self, request):
        po = get_object_or_404(PurchaseOrder, pk=request.GET.get("po"))
        form = VendorBillForm()
        return render(request, self.template_name, {"form": form, "po": po})

    def post(self, request):
        po = get_object_or_404(PurchaseOrder, pk=request.GET.get("po") or request.POST.get("po"))
        # vendor/po must be set before is_valid() runs, since VendorBill.clean()
        # (invoked via the form's instance.full_clean()) reads them for the
        # duplicate-invoice-per-FY check.
        form = VendorBillForm(request.POST, request.FILES, instance=VendorBill(vendor=po.vendor, po=po))
        if form.is_valid():
            bill = form.save(commit=False)
            bill.created_by = request.user
            bill.save()
            for po_line in po.lines.all():
                available = po_line.pending_billable_quantity
                if available > 0:
                    VendorBillLine.objects.create(
                        bill=bill, po_line=po_line, quantity_billed=available,
                        rate_billed=po_line.rate, gst_rate=po_line.gst_rate, created_by=request.user,
                    )
            messages.success(request, f"Draft bill {bill.bill_number} created.")
            return redirect("billing:bill_detail", pk=bill.pk)
        return render(request, self.template_name, {"form": form, "po": po})


class VendorBillDetailView(BillAccessRequiredMixin, DetailView):
    model = VendorBill
    template_name = "billing/bill_detail.html"
    context_object_name = "bill"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        bill = self.object
        ctx["formset"] = VendorBillLineFormSet(queryset=bill.lines.select_related("po_line__item"))
        ctx["reason_form"] = ReasonForm()
        ctx["remark_form"] = BillRemarkForm()
        ctx["can_override"] = roles.can_override_mismatch(self.request.user)
        ctx["allocation_form"] = PaymentAllocationForm(vendor=bill.vendor)
        return ctx


class BillLinesUpdateView(BillAccessRequiredMixin, View):
    def post(self, request, pk):
        bill = get_object_or_404(VendorBill, pk=pk)
        if not bill.is_editable:
            messages.error(request, "This bill has already been matched and can no longer be edited.")
            return redirect("billing:bill_detail", pk=pk)
        formset = VendorBillLineFormSet(request.POST, queryset=bill.lines.select_related("po_line__item"))
        if formset.is_valid():
            formset.save()
            messages.success(request, "Bill lines updated.")
        else:
            messages.error(request, "Could not save: please check the entered values.")
        return redirect("billing:bill_detail", pk=pk)


class BillSubmitMatchView(BillAccessRequiredMixin, View):
    def post(self, request, pk):
        bill = get_object_or_404(VendorBill, pk=pk)
        try:
            bill.submit_for_matching(request.user)
            bill.refresh_from_db()
            if bill.status == VendorBill.Status.APPROVED_FOR_PAYMENT:
                messages.success(request, f"{bill.bill_number} matched and approved for payment.")
            else:
                messages.warning(request, f"{bill.bill_number} has a mismatch — see the comparison below.")
        except InvalidStatusTransition as exc:
            messages.error(request, str(exc))
        return redirect("billing:bill_detail", pk=pk)


class BillOverrideView(MismatchOverrideRequiredMixin, View):
    def post(self, request, pk):
        bill = get_object_or_404(VendorBill, pk=pk)
        reason = request.POST.get("text", "")
        try:
            bill.override_mismatch(request.user, reason)
            messages.success(request, f"{bill.bill_number} approved for payment (override).")
        except InvalidStatusTransition as exc:
            messages.error(request, str(exc))
        return redirect("billing:bill_detail", pk=pk)


class BillCancelView(BillAccessRequiredMixin, View):
    def post(self, request, pk):
        bill = get_object_or_404(VendorBill, pk=pk)
        reason = request.POST.get("text", "")
        try:
            bill.cancel(request.user, reason)
            messages.success(request, f"{bill.bill_number} cancelled.")
        except InvalidStatusTransition as exc:
            messages.error(request, str(exc))
        return redirect("billing:bill_detail", pk=pk)


class BillRemarkCreateView(BillAccessRequiredMixin, View):
    def post(self, request, pk):
        bill = get_object_or_404(VendorBill, pk=pk)
        form = BillRemarkForm(request.POST)
        if form.is_valid():
            remark = form.save(commit=False)
            remark.bill = bill
            remark.author = request.user
            remark.created_by = request.user
            remark.save()
            messages.success(request, "Remark added.")
        return redirect("billing:bill_detail", pk=pk)


class DebitNoteFromCandidateView(BillAccessRequiredMixin, View):
    def post(self, request, candidate_pk):
        candidate = get_object_or_404(DebitNoteCandidate, pk=candidate_pk)
        if candidate.resolved:
            messages.error(request, "This candidate has already been resolved.")
            return redirect("stores:grn_detail", pk=candidate.grn_line.grn_id)
        po_line = candidate.grn_line.po_line
        vendor = po_line.po.vendor
        amount = (candidate.qty * po_line.rate).quantize(Decimal("0.01"))
        DebitNote.objects.create(
            vendor=vendor, grn_line=candidate.grn_line, amount=amount, reason=candidate.reason or "Rejected/short receipt",
            created_by=request.user,
        )
        candidate.resolved = True
        candidate.save(update_fields=["resolved"])
        messages.success(request, f"Debit note for ₹{amount} created against {vendor.name}.")
        return redirect("stores:grn_detail", pk=candidate.grn_line.grn_id)


class DebitNoteAdjustView(BillAccessRequiredMixin, View):
    def post(self, request, pk):
        note = get_object_or_404(DebitNote, pk=pk)
        try:
            note.adjust()
            messages.success(request, "Debit note marked as adjusted.")
        except InvalidStatusTransition as exc:
            messages.error(request, str(exc))
        return redirect("billing:vendor_ledger", pk=note.vendor_id)


# --- Payments ------------------------------------------------------------

class PaymentListView(BillAccessRequiredMixin, ListView):
    model = Payment
    template_name = "billing/payment_list.html"
    context_object_name = "payments"
    paginate_by = PAGE_SIZE

    def get_queryset(self):
        return Payment.objects.select_related("vendor")


class PaymentCreateView(BillAccessRequiredMixin, CreateView):
    model = Payment
    form_class = PaymentForm
    template_name = "billing/payment_form.html"

    def form_valid(self, form):
        form.instance.created_by = self.request.user
        response = super().form_valid(form)
        messages.success(self.request, f"Payment {self.object.payment_number} recorded.")
        return response

    def get_success_url(self):
        return reverse("billing:payment_detail", args=[self.object.pk])


class PaymentDetailView(BillAccessRequiredMixin, DetailView):
    model = Payment
    template_name = "billing/payment_detail.html"
    context_object_name = "payment"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["allocation_form"] = PaymentAllocationForm(vendor=self.object.vendor)
        return ctx


class PaymentAllocationCreateView(BillAccessRequiredMixin, View):
    def post(self, request, pk):
        payment = get_object_or_404(Payment, pk=pk)
        # `payment` must be set on the instance before is_valid() runs, since
        # PaymentAllocation.clean() (invoked via instance.full_clean()) reads
        # self.payment.unallocated_amount.
        form = PaymentAllocationForm(request.POST, vendor=payment.vendor, instance=PaymentAllocation(payment=payment))
        if form.is_valid():
            allocation = form.save(commit=False)
            allocation.created_by = request.user
            try:
                allocation.full_clean()
            except Exception as exc:
                messages.error(request, "; ".join(getattr(exc, "messages", [str(exc)])))
                return redirect("billing:payment_detail", pk=pk)
            allocation.save()
            messages.success(request, f"₹{allocation.amount} allocated to {allocation.bill.bill_number}.")
        else:
            messages.error(request, "Could not allocate: " + "; ".join(
                f"{field}: {', '.join(errs)}" for field, errs in form.errors.items()
            ))
        return redirect("billing:payment_detail", pk=pk)


class PaymentAllocationDeleteView(BillAccessRequiredMixin, View):
    def post(self, request, pk, allocation_pk):
        allocation = get_object_or_404(PaymentAllocation, pk=allocation_pk, payment_id=pk)
        allocation.delete()
        messages.success(request, "Allocation removed.")
        return redirect("billing:payment_detail", pk=pk)


# --- Ledger & aging --------------------------------------------------

class VendorFullLedgerView(BillAccessRequiredMixin, DetailView):
    model = Vendor
    template_name = "billing/vendor_ledger.html"
    context_object_name = "vendor"

    def get_context_data(self, **kwargs):
        from billing.services import vendor_ledger_entries
        ctx = super().get_context_data(**kwargs)
        ctx["entries"] = vendor_ledger_entries(self.object)
        return ctx


class PayablesAgingView(BillAccessRequiredMixin, View):
    template_name = "billing/payables_aging.html"

    def get(self, request):
        from billing.services import payables_aging
        from masters.models import ItemCategory, Site

        site_id = request.GET.get("site", "")
        category_id = request.GET.get("category", "")
        site = Site.objects.filter(pk=site_id).first() if site_id else None
        category = ItemCategory.objects.filter(pk=category_id).first() if category_id else None
        data = payables_aging(site=site, category=category)
        return render(request, self.template_name, {
            **data, "sites": Site.objects.all(), "categories": ItemCategory.objects.all(),
            "site_id": site_id, "category_id": category_id,
        })


class PayablesAgingExcelView(BillAccessRequiredMixin, View):
    def get(self, request):
        from billing.exports import payables_aging_workbook
        from billing.services import payables_aging

        data = payables_aging()
        workbook = payables_aging_workbook(data)
        from django.http import HttpResponse
        response = HttpResponse(content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
        response["Content-Disposition"] = 'attachment; filename="payables_aging.xlsx"'
        workbook.save(response)
        return response


class VendorLedgerExcelView(BillAccessRequiredMixin, View):
    def get(self, request, pk):
        from django.http import HttpResponse

        from billing.exports import vendor_ledger_workbook
        from billing.services import vendor_ledger_entries

        vendor = get_object_or_404(Vendor, pk=pk)
        entries = vendor_ledger_entries(vendor)
        workbook = vendor_ledger_workbook(vendor, entries)
        response = HttpResponse(content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
        response["Content-Disposition"] = f'attachment; filename="{vendor.code}_ledger.xlsx"'
        workbook.save(response)
        return response
