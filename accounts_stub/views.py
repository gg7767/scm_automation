from django.contrib import messages
from django.contrib.auth.decorators import login_required, user_passes_test
from django.contrib.auth.models import User, Group
from django.shortcuts import render, redirect, get_object_or_404
from django.urls import reverse_lazy
from django.views.decorators.http import require_http_methods
from django.db.models import Q

from accounts_stub.forms import UserCreationForm, UserEditForm, RoleAssignmentForm, UserSearchForm
from accounts_stub.models import UserProfile
from accounts_stub.roles import ALL_ROLES


def is_superuser(user):
    """Check if user is a superuser."""
    return user.is_superuser


@login_required
@user_passes_test(is_superuser)
def user_list(request):
    """List all users with search and filter."""
    form = UserSearchForm(request.GET or None)
    users = User.objects.all().select_related('profile')
    
    # Apply search filter
    search = request.GET.get('search', '').strip()
    if search:
        users = users.filter(
            Q(username__icontains=search) |
            Q(email__icontains=search) |
            Q(first_name__icontains=search) |
            Q(last_name__icontains=search)
        )
    
    # Apply status filter
    status = request.GET.get('status', 'active')
    if status == 'active':
        users = users.filter(is_active=True)
    elif status == 'inactive':
        users = users.filter(is_active=False)
    
    # Apply role filter
    role_id = request.GET.get('role')
    if role_id:
        users = users.filter(groups__id=role_id)
    
    # Order and paginate
    users = users.order_by('username')
    
    context = {
        'users': users,
        'form': form,
        'search': search,
        'status': status,
        'role_id': role_id,
    }
    return render(request, 'accounts_stub/user_list.html', context)


@login_required
@user_passes_test(is_superuser)
@require_http_methods(["GET", "POST"])
def user_create(request):
    """Create a new user."""
    if request.method == 'POST':
        form = UserCreationForm(request.POST)
        if form.is_valid():
            user = form.save()
            # Create UserProfile and assign site
            site = form.cleaned_data.get('site')
            UserProfile.objects.get_or_create(user=user, defaults={'site': site})
            messages.success(request, f'User "{user.username}" created successfully.')
            return redirect('accounts_stub:user_detail', pk=user.pk)
    else:
        form = UserCreationForm()
    
    context = {'form': form}
    return render(request, 'accounts_stub/user_form.html', context)


@login_required
@user_passes_test(is_superuser)
def user_detail(request, pk):
    """View and edit a user's details and roles."""
    user = get_object_or_404(User, pk=pk)
    user_roles = list(user.groups.values_list('name', flat=True))
    
    if request.method == 'POST':
        action = request.POST.get('action')
        
        if action == 'edit_profile':
            form = UserEditForm(request.POST, instance=user)
            if form.is_valid():
                form.save()
                messages.success(request, f'User "{user.username}" updated successfully.')
                return redirect('accounts_stub:user_detail', pk=user.pk)
        
        elif action == 'assign_roles':
            # Get selected roles from checkboxes
            selected_roles = [role for role in ALL_ROLES if request.POST.get(f'role_{role}')]
            user.groups.clear()
            for role_name in selected_roles:
                group, _ = Group.objects.get_or_create(name=role_name)
                user.groups.add(group)
            messages.success(request, f'Roles updated for "{user.username}".')
            return redirect('accounts_stub:user_detail', pk=user.pk)
        
        elif action == 'toggle_status':
            user.is_active = not user.is_active
            user.save()
            status = "activated" if user.is_active else "deactivated"
            messages.success(request, f'User "{user.username}" {status}.')
            return redirect('accounts_stub:user_detail', pk=user.pk)
    
    edit_form = UserEditForm(instance=user)
    role_form_data = {f'role_{role}': role in user_roles for role in ALL_ROLES}
    
    context = {
        'user': user,
        'edit_form': edit_form,
        'all_roles': ALL_ROLES,
        'user_roles': user_roles,
        'role_form_data': role_form_data,
    }
    return render(request, 'accounts_stub/user_detail.html', context)


@login_required
@user_passes_test(is_superuser)
@require_http_methods(["POST"])
def user_delete(request, pk):
    """Delete a user."""
    user = get_object_or_404(User, pk=pk)
    username = user.username
    user.delete()
    messages.success(request, f'User "{username}" deleted successfully.')
    return redirect('accounts_stub:user_list')
