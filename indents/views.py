from django.contrib import messages
from django.core.exceptions import PermissionDenied
from django.db.models import Q
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.template.loader import render_to_string
from django.urls import reverse
from django.views.generic import CreateView, DetailView, ListView, UpdateView, View

from accounts_stub import roles
from indents.forms import IndentForm, IndentLineForm, ReasonForm
from indents.models import Indent, IndentLine, InvalidStatusTransition
from indents.permissions import (
    IndentAccessRequiredMixin,
    IndentConversionRequiredMixin,
    visible_indent_queryset,
)
from masters.models import Item

PAGE_SIZE = 20


def _lines_section_response(request, indent):
    html = render_to_string(
        "indents/_indent_lines_section.html",
        {"indent": indent, "line_form": IndentLineForm()},
        request=request,
    )
    return HttpResponse(html)


class IndentListView(IndentAccessRequiredMixin, ListView):
    model = Indent
    template_name = "indents/indent_list.html"
    context_object_name = "indents"
    paginate_by = PAGE_SIZE

    def get_queryset(self):
        qs = visible_indent_queryset(self.request.user, Indent.objects.select_related("site", "raised_by"))
        q = self.request.GET.get("q", "").strip()
        if q:
            qs = qs.filter(Q(indent_number__icontains=q) | Q(project_name__icontains=q))
        status = self.request.GET.get("status", "")
        if status:
            qs = qs.filter(status=status)
        return qs

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["q"] = self.request.GET.get("q", "")
        ctx["status"] = self.request.GET.get("status", "")
        ctx["status_choices"] = Indent.Status.choices
        return ctx


class IndentCreateView(IndentAccessRequiredMixin, CreateView):
    model = Indent
    form_class = IndentForm
    template_name = "indents/indent_form.html"

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        if roles.is_site_restricted(self.request.user):
            kwargs["restrict_site"] = roles.user_site(self.request.user)
        return kwargs

    def form_valid(self, form):
        form.instance.raised_by = self.request.user
        form.instance.created_by = self.request.user
        response = super().form_valid(form)
        messages.success(self.request, f"Draft indent {self.object.indent_number} created. Add items to submit.")
        return response

    def get_success_url(self):
        return reverse("indents:indent_detail", args=[self.object.pk])


class IndentDetailView(IndentAccessRequiredMixin, DetailView):
    model = Indent
    template_name = "indents/indent_detail.html"
    context_object_name = "indent"

    def get_queryset(self):
        return visible_indent_queryset(self.request.user, Indent.objects.select_related("site", "raised_by"))

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        indent = self.object
        user = self.request.user
        ctx["line_form"] = IndentLineForm()
        ctx["reason_form"] = ReasonForm()
        ctx["can_manage_po"] = roles.can_manage_purchase_orders(user)
        ctx["estimated_value"] = indent.estimated_value()
        from purchase.models import ApprovalRule
        ctx["can_approve"] = (
            indent.status == Indent.Status.PENDING_APPROVAL
            and ApprovalRule.can_user_approve(user, indent.estimated_value(), doc_type=ApprovalRule.DocType.INDENT)
        )
        return ctx


class IndentLineCreateView(IndentAccessRequiredMixin, View):
    def post(self, request, pk):
        indent = get_object_or_404(Indent, pk=pk)
        if not indent.is_editable:
            messages.error(request, "This indent is no longer editable.")
            return _lines_section_response(request, indent)
        form = IndentLineForm(request.POST)
        if form.is_valid():
            line = form.save(commit=False)
            line.indent = indent
            line.created_by = request.user
            line.save()
        else:
            messages.error(request, "Could not add item: " + "; ".join(
                f"{field}: {', '.join(errs)}" for field, errs in form.errors.items()
            ))
        indent.refresh_from_db()
        return _lines_section_response(request, indent)


class IndentLineDeleteView(IndentAccessRequiredMixin, View):
    def post(self, request, pk, line_pk):
        indent = get_object_or_404(Indent, pk=pk)
        line = get_object_or_404(IndentLine, pk=line_pk, indent=indent)
        if not indent.is_editable:
            messages.error(request, "This indent is no longer editable.")
            return _lines_section_response(request, indent)
        line.delete()
        indent.refresh_from_db()
        return _lines_section_response(request, indent)


class IndentLineRejectView(IndentConversionRequiredMixin, View):
    def post(self, request, pk, line_pk):
        indent = get_object_or_404(Indent, pk=pk)
        line = get_object_or_404(IndentLine, pk=line_pk, indent=indent)
        reason = request.POST.get("text", "")
        try:
            line.reject_remaining(request.user, reason)
            messages.success(request, "Line rejected.")
        except InvalidStatusTransition as exc:
            messages.error(request, str(exc))
        return redirect("indents:indent_detail", pk=pk)


class ItemSearchView(IndentAccessRequiredMixin, View):
    def get(self, request):
        q = request.GET.get("q", "").strip()
        items = []
        if q:
            items = list(
                Item.objects.filter(active=True).filter(
                    Q(name__icontains=q) | Q(code__icontains=q) | Q(aliases__alias_name__icontains=q)
                ).distinct()[:10]
            )
        return render(request, "indents/_item_search_results.html", {"items": items})


class IndentSubmitView(IndentAccessRequiredMixin, View):
    def post(self, request, pk):
        indent = get_object_or_404(Indent, pk=pk)
        try:
            indent.submit_for_approval(request.user)
            messages.success(request, f"{indent.indent_number} submitted for approval.")
        except InvalidStatusTransition as exc:
            messages.error(request, str(exc))
        return redirect("indents:indent_detail", pk=pk)


class IndentApproveView(IndentConversionRequiredMixin, View):
    def post(self, request, pk):
        indent = get_object_or_404(Indent, pk=pk)
        from purchase.models import ApprovalRule
        if not ApprovalRule.can_user_approve(request.user, indent.estimated_value(), doc_type=ApprovalRule.DocType.INDENT):
            raise PermissionDenied("You are not authorized to approve this indent.")
        comment = request.POST.get("text", "")
        try:
            indent.approve(request.user, comment=comment)
            messages.success(request, f"{indent.indent_number} approved.")
        except InvalidStatusTransition as exc:
            messages.error(request, str(exc))
        return redirect("indents:indent_detail", pk=pk)


class IndentRejectView(IndentConversionRequiredMixin, View):
    def post(self, request, pk):
        indent = get_object_or_404(Indent, pk=pk)
        from purchase.models import ApprovalRule
        if not ApprovalRule.can_user_approve(request.user, indent.estimated_value(), doc_type=ApprovalRule.DocType.INDENT):
            raise PermissionDenied("You are not authorized to act on this indent.")
        reason = request.POST.get("text", "")
        try:
            indent.reject(request.user, reason)
            messages.success(request, f"{indent.indent_number} rejected.")
        except InvalidStatusTransition as exc:
            messages.error(request, str(exc))
        return redirect("indents:indent_detail", pk=pk)


class IndentCancelView(IndentAccessRequiredMixin, View):
    def post(self, request, pk):
        indent = get_object_or_404(Indent, pk=pk)
        reason = request.POST.get("text", "")
        try:
            indent.cancel(request.user, reason)
            messages.success(request, f"{indent.indent_number} cancelled.")
        except InvalidStatusTransition as exc:
            messages.error(request, str(exc))
        return redirect("indents:indent_detail", pk=pk)


class IndentConvertView(IndentConversionRequiredMixin, View):
    """Convert one or more approved indent lines (optionally spanning
    multiple indents for the same site+vendor) into a draft PO."""

    template_name = "indents/indent_convert.html"

    def get(self, request, pk):
        indent = get_object_or_404(Indent, pk=pk)
        if indent.status not in (Indent.Status.APPROVED, Indent.Status.PARTIALLY_ORDERED):
            messages.error(request, "Only approved indents can be converted to a PO.")
            return redirect("indents:indent_detail", pk=pk)
        from masters.models import Vendor
        open_lines = [line for line in indent.lines.all() if not line.is_resolved]
        return render(request, self.template_name, {
            "indent": indent, "open_lines": open_lines, "vendors": Vendor.objects.filter(status="active"),
        })

    def post(self, request, pk):
        indent = get_object_or_404(Indent, pk=pk)
        from masters.models import Vendor
        from indents.services import convert_indent_lines_to_po

        vendor_id = request.POST.get("vendor")
        vendor = get_object_or_404(Vendor, pk=vendor_id)
        line_qtys = {}
        for line in indent.lines.all():
            raw = request.POST.get(f"qty_{line.pk}", "").strip()
            if raw:
                try:
                    from decimal import Decimal, InvalidOperation
                    qty = Decimal(raw)
                    if qty > 0:
                        line_qtys[line] = qty
                except InvalidOperation:
                    continue

        if not line_qtys:
            messages.error(request, "Select at least one item quantity to order.")
            return redirect("indents:indent_convert", pk=pk)

        po = convert_indent_lines_to_po(line_qtys, vendor=vendor, site=indent.site, user=request.user)
        messages.success(request, f"Draft PO {po.po_number} created from {indent.indent_number}.")
        return redirect("purchase:po_detail", pk=po.pk)
