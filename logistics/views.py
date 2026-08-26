from django.db.models import Count, Sum
from django.views.generic import CreateView, DetailView, ListView, View
from django.shortcuts import render

from logistics.forms import TransportTripForm
from logistics.models import TransportTrip
from logistics.permissions import TripAccessRequiredMixin

PAGE_SIZE = 20


class TripListView(TripAccessRequiredMixin, ListView):
    model = TransportTrip
    template_name = "logistics/trip_list.html"
    context_object_name = "trips"
    paginate_by = PAGE_SIZE

    def get_queryset(self):
        qs = TransportTrip.objects.select_related("transporter", "to_site")
        site_id = self.request.GET.get("site", "")
        if site_id:
            qs = qs.filter(to_site_id=site_id)
        without_pod = self.request.GET.get("without_pod", "")
        if without_pod:
            qs = qs.filter(pod_photo="")
        return qs

    def get_context_data(self, **kwargs):
        from masters.models import Site
        ctx = super().get_context_data(**kwargs)
        ctx["site"] = self.request.GET.get("site", "")
        ctx["without_pod"] = self.request.GET.get("without_pod", "")
        ctx["sites"] = Site.objects.all()
        return ctx


class TripCreateView(TripAccessRequiredMixin, CreateView):
    model = TransportTrip
    form_class = TransportTripForm
    template_name = "logistics/trip_form.html"

    def form_valid(self, form):
        form.instance.created_by = self.request.user
        return super().form_valid(form)

    def get_success_url(self):
        from django.urls import reverse
        return reverse("logistics:trip_detail", args=[self.object.pk])


class TripDetailView(TripAccessRequiredMixin, DetailView):
    model = TransportTrip
    template_name = "logistics/trip_detail.html"
    context_object_name = "trip"


class FreightReportView(TripAccessRequiredMixin, View):
    template_name = "logistics/freight_report.html"

    def get(self, request):
        by_site = (
            TransportTrip.objects.values("to_site__code")
            .annotate(total_freight=Sum("freight_amount"), trip_count=Count("id"))
            .order_by("-total_freight")
        )
        by_transporter = (
            TransportTrip.objects.values("transporter__name")
            .annotate(total_freight=Sum("freight_amount"), trip_count=Count("id"))
            .order_by("-total_freight")
        )
        trips_without_pod = TransportTrip.objects.filter(pod_photo="").select_related("to_site", "transporter")
        return render(request, self.template_name, {
            "by_site": by_site, "by_transporter": by_transporter, "trips_without_pod": trips_without_pod,
        })
