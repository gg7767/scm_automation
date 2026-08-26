from django.contrib import messages
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse
from django.views.generic import CreateView, DetailView, ListView, View

from assets.forms import MachineDeploymentForm, MachineForm, MachineLogForm
from assets.models import Machine, MachineDeployment, MachineLog, MaintenanceSchedule
from assets.permissions import AssetAccessRequiredMixin, AssetManageRequiredMixin

PAGE_SIZE = 20


class MachineListView(AssetAccessRequiredMixin, ListView):
    """'Where is everything' — machines grouped by current site."""
    model = Machine
    template_name = "assets/machine_list.html"
    context_object_name = "machines"
    paginate_by = PAGE_SIZE

    def get_queryset(self):
        qs = Machine.objects.select_related().prefetch_related("deployments__site")
        status = self.request.GET.get("status", "")
        if status:
            qs = qs.filter(status=status)
        return qs

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["status"] = self.request.GET.get("status", "")
        ctx["status_choices"] = Machine.Status.choices
        return ctx


class MachineCreateView(AssetManageRequiredMixin, CreateView):
    model = Machine
    form_class = MachineForm
    template_name = "assets/machine_form.html"

    def form_valid(self, form):
        form.instance.created_by = self.request.user
        return super().form_valid(form)

    def get_success_url(self):
        return reverse("assets:machine_detail", args=[self.object.pk])


class MachineDetailView(AssetAccessRequiredMixin, DetailView):
    model = Machine
    template_name = "assets/machine_detail.html"
    context_object_name = "machine"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["deployment_form"] = MachineDeploymentForm()
        ctx["log_form"] = MachineLogForm()
        ctx["maintenance_schedules"] = self.object.maintenance_schedules.all()
        return ctx


class MachineDeployView(AssetManageRequiredMixin, View):
    def post(self, request, pk):
        machine = get_object_or_404(Machine, pk=pk)
        current = machine.current_deployment
        if current:
            current.close()
        form = MachineDeploymentForm(request.POST)
        if form.is_valid():
            deployment = form.save(commit=False)
            deployment.machine = machine
            deployment.created_by = request.user
            deployment.save()
            messages.success(request, f"{machine.code} deployed to {deployment.site.code}.")
        else:
            messages.error(request, "Could not record deployment.")
        return redirect("assets:machine_detail", pk=pk)


class MachineLogCreateView(AssetAccessRequiredMixin, View):
    def post(self, request, pk):
        machine = get_object_or_404(Machine, pk=pk)
        form = MachineLogForm(request.POST)
        if form.is_valid():
            log = form.save(commit=False)
            log.machine = machine
            log.created_by = request.user
            try:
                log.save()
                messages.success(request, "Log entry added.")
            except Exception as exc:
                messages.error(request, str(exc))
        else:
            messages.error(request, "Could not add log entry.")
        return redirect("assets:machine_detail", pk=pk)


class MaintenanceDueReportView(AssetAccessRequiredMixin, View):
    template_name = "assets/maintenance_due.html"

    def get(self, request):
        from django.shortcuts import render
        due = [s for s in MaintenanceSchedule.objects.select_related("machine") if s.is_due]
        return render(request, self.template_name, {"due_schedules": due})
