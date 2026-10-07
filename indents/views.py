from decimal import Decimal, InvalidOperation
from django.contrib import messages
from django.core.exceptions import PermissionDenied
from django.db.models import Q
from django.http import HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils.dateparse import parse_date
from django.views.generic import DetailView, ListView, View

from accounts_stub import roles
from indents.forms import ReasonForm
from indents.models import Indent, IndentLine, InvalidStatusTransition
from indents.permissions import (
    IndentAccessRequiredMixin,
    IndentConversionRequiredMixin,
    visible_indent_queryset,
)
from masters.models import Item, Site
from stores.models import StockBalance

PAGE_SIZE = 20


def _lines_section_response(request, indent):
    unit_choices = Item.Unit.choices
    existing_items_qs = Item.objects.filter(active=True).order_by("name")

    stock_map = {
        sb.item_id: sb.quantity
        for sb in StockBalance.objects.filter(site=indent.site)
    }

    items_data = []
    for item in existing_items_qs:
        items_data.append({
            "id": item.pk,
            "name": item.name,
            "code": item.code,
            "unit": item.unit,
            "unit_display": item.get_unit_display(),
            "stock": stock_map.get(item.pk, Decimal("0.000")),
        })

    html = render_to_string(
        "indents/_indent_lines_section.html",
        {
            "indent": indent,
            "unit_choices": unit_choices,
            "existing_items": items_data,
        },
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


class IndentCreateView(IndentAccessRequiredMixin, View):
    def get_target_site(self, user):
        if roles.is_site_restricted(user):
            site = roles.user_site(user)
            if site:
                return site
        if hasattr(user, "profile") and user.profile.site:
            return user.profile.site
        return Site.objects.filter(active=True).first() or Site.objects.first()

    def get(self, request, *args, **kwargs):
        site = self.get_target_site(request.user)
        if not site:
            messages.error(request, "No site available to raise an indent.")
            return redirect("indents:indent_list")

        indent = Indent.objects.create(
            site=site,
            raised_by=request.user,
            created_by=request.user,
            status=Indent.Status.DRAFT,
        )
        messages.success(request, f"Draft indent {indent.indent_number} created.")
        return redirect("indents:indent_detail", pk=indent.pk)

    def post(self, request, *args, **kwargs):
        return self.get(request, *args, **kwargs)


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
        ctx["reason_form"] = ReasonForm()
        ctx["can_manage_po"] = roles.can_manage_purchase_orders(user)
        ctx["estimated_value"] = indent.estimated_value()
        ctx["unit_choices"] = Item.Unit.choices

        stock_map = {
            sb.item_id: sb.quantity
            for sb in StockBalance.objects.filter(site=indent.site)
        }

        items_data = []
        for item in Item.objects.filter(active=True).order_by("name"):
            items_data.append({
                "id": item.pk,
                "name": item.name,
                "code": item.code,
                "unit": item.unit,
                "unit_display": item.get_unit_display(),
                "stock": stock_map.get(item.pk, Decimal("0.000")),
            })
        ctx["existing_items"] = items_data

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

        item_description = request.POST.get("item_description", "").strip()
        existing_item_id = request.POST.get("existing_item_id", "").strip()
        unit = request.POST.get("unit", "").strip()
        quantity_str = request.POST.get("quantity", "").strip()
        present_stock_str = request.POST.get("present_stock", "0").strip()
        required_by_date_str = request.POST.get("required_by_date", "").strip()
        purpose = request.POST.get("purpose", "").strip()

        if not item_description:
            messages.error(request, "Item description is required.")
            return _lines_section_response(request, indent)

        try:
            quantity = Decimal(quantity_str)
            if quantity <= 0:
                raise ValueError()
        except (ValueError, TypeError, InvalidOperation):
            messages.error(request, "Please enter a valid positive quantity.")
            return _lines_section_response(request, indent)

        present_stock = Decimal("0.000")
        if present_stock_str:
            try:
                present_stock = Decimal(present_stock_str)
            except (ValueError, TypeError, InvalidOperation):
                pass

        required_by_date = None
        if required_by_date_str:
            try:
                required_by_date = parse_date(required_by_date_str)
            except Exception:
                pass

        item = None
        if existing_item_id:
            item = Item.objects.filter(pk=existing_item_id, active=True).first()

        if not item:
            item = Item.objects.filter(name__iexact=item_description, active=True).first()

        if not item:
            if not unit:
                unit = Item.Unit.NOS
            from masters.models import ItemCategory
            category = ItemCategory.objects.first()
            if not category:
                category = ItemCategory.objects.create(name="General")
            item = Item.objects.create(
                name=item_description,
                unit=unit,
                category=category,
                gst_rate=Decimal("18.00"),
                active=True,
            )
        else:
            unit = item.unit
            sb = StockBalance.objects.filter(site=indent.site, item=item).first()
            if sb:
                present_stock = sb.quantity

        line = IndentLine.objects.create(
            indent=indent,
            item=item,
            quantity=quantity,
            unit=unit,
            present_stock=present_stock,
            required_by_date=required_by_date,
            purpose=purpose,
            created_by=request.user,
        )

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
        site_id = request.GET.get("site_id")
        items = []
        if q:
            items_qs = Item.objects.filter(active=True).filter(
                Q(name__icontains=q) | Q(code__icontains=q) | Q(aliases__alias_name__icontains=q)
            ).distinct()[:15]

            site = None
            if site_id:
                site = Site.objects.filter(pk=site_id).first()

            items = []
            for item in items_qs:
                stock_qty = Decimal("0.000")
                if site:
                    sb = StockBalance.objects.filter(site=site, item=item).first()
                    if sb:
                        stock_qty = sb.quantity
                items.append({
                    "id": item.pk,
                    "name": item.name,
                    "code": item.code,
                    "unit": item.unit,
                    "unit_display": item.get_unit_display(),
                    "stock": stock_qty,
                })
        return render(request, "indents/_item_search_results.html", {"items": items})


class VendorRatesView(IndentAccessRequiredMixin, View):
    def get(self, request):
        vendor_id = request.GET.get("vendor_id")
        rates = {}
        if vendor_id:
            from masters.models import Vendor, RateContract
            from purchase.models import PurchaseOrderLine
            vendor = Vendor.objects.filter(pk=vendor_id).first()
            if vendor:
                raw_item_ids = request.GET.getlist("item_ids")
                item_ids = []
                for raw in raw_item_ids:
                    for part in raw.split(","):
                        part = part.strip()
                        if part.isdigit():
                            item_ids.append(int(part))

                for item_id in item_ids:
                    # 1. Check last PO line for this vendor & item
                    last_po_line = PurchaseOrderLine.objects.filter(
                        po__vendor=vendor, item_id=item_id
                    ).order_by("-created_at").first()

                    if last_po_line:
                        rates[str(item_id)] = str(last_po_line.rate)
                    else:
                        # 2. Check active rate contract
                        contract_rate = RateContract.current_rate(vendor, item_id)
                        if contract_rate is not None:
                            rates[str(item_id)] = str(contract_rate)
                        else:
                            rates[str(item_id)] = ""
        return JsonResponse(rates)


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
        line_rates = {}

        for line in indent.lines.all():
            raw_qty = request.POST.get(f"qty_{line.pk}", "").strip()
            raw_rate = request.POST.get(f"rate_{line.pk}", "").strip()

            if raw_qty:
                try:
                    qty = Decimal(raw_qty)
                    if qty > 0:
                        line_qtys[line] = qty
                        if raw_rate:
                            try:
                                line_rates[line] = Decimal(raw_rate)
                            except InvalidOperation:
                                line_rates[line] = Decimal("0.00")
                        else:
                            line_rates[line] = None
                except InvalidOperation:
                    continue

        if not line_qtys:
            messages.error(request, "Select at least one item quantity to order.")
            return redirect("indents:indent_convert", pk=pk)

        po = convert_indent_lines_to_po(
            line_qtys, vendor=vendor, site=indent.site, user=request.user, line_rates=line_rates
        )
        messages.success(request, f"Draft PO {po.po_number} created from {indent.indent_number}.")
        return redirect("purchase:po_detail", pk=po.pk)
