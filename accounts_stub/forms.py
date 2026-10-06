from django import forms
from django.contrib.auth.models import User, Group
from django.core.exceptions import ValidationError
from accounts_stub.models import UserProfile
from masters.models import Site
from accounts_stub.roles import ALL_ROLES


class UserCreationForm(forms.ModelForm):
    """Form to create a new user with profile."""
    password = forms.CharField(
        widget=forms.PasswordInput,
        help_text="Enter a strong password"
    )
    confirm_password = forms.CharField(
        widget=forms.PasswordInput,
        help_text="Confirm password"
    )
    site = forms.ModelChoiceField(
        queryset=Site.objects.filter(active=True),
        required=False,
        help_text="Assign to a site (required for Site Members only)"
    )
    
    class Meta:
        model = User
        fields = ['username', 'email', 'first_name', 'last_name']
        help_texts = {
            'username': 'Required. 150 characters or fewer. Letters, digits and @/./+/-/_ only.',
            'email': 'Valid email address',
            'first_name': 'User\'s first name',
            'last_name': 'User\'s last name',
        }
    
    def clean(self):
        cleaned_data = super().clean()
        password = cleaned_data.get("password")
        confirm_password = cleaned_data.get("confirm_password")
        
        if password and confirm_password:
            if password != confirm_password:
                raise ValidationError("Passwords do not match.")
        
        username = cleaned_data.get("username")
        if username and User.objects.filter(username=username).exists():
            raise ValidationError("Username already exists.")
        
        return cleaned_data
    
    def save(self, commit=True):
        user = super().save(commit=False)
        user.set_password(self.cleaned_data["password"])
        if commit:
            user.save()
        return user


class UserEditForm(forms.ModelForm):
    """Form to edit existing user details."""
    site = forms.ModelChoiceField(
        queryset=Site.objects.filter(active=True),
        required=False,
        help_text="Assign to a site (required for Site Members only)"
    )
    
    class Meta:
        model = User
        fields = ['email', 'first_name', 'last_name', 'is_active']
    
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Pre-fill site if user has a profile
        if self.instance and hasattr(self.instance, 'profile'):
            self.fields['site'].initial = self.instance.profile.site
    
    def save(self, commit=True):
        user = super().save(commit=commit)
        # Save site to profile
        if hasattr(user, 'profile'):
            user.profile.site = self.cleaned_data.get('site')
            user.profile.save()
        return user


class RoleAssignmentForm(forms.Form):
    """Form to assign roles to a user."""
    roles = forms.MultipleChoiceField(
        choices=[(role, role) for role in ALL_ROLES],
        widget=forms.CheckboxSelectMultiple,
        required=False,
        help_text="Select one or more roles for this user"
    )
    
    def __init__(self, *args, **kwargs):
        self.user = kwargs.pop('user', None)
        super().__init__(*args, **kwargs)
    
    def save(self):
        if not self.user:
            return
        
        selected_roles = self.cleaned_data.get('roles', [])
        # Remove all groups
        self.user.groups.clear()
        
        # Add selected groups
        for role_name in selected_roles:
            group, _ = Group.objects.get_or_create(name=role_name)
            self.user.groups.add(group)


class UserSearchForm(forms.Form):
    """Form for searching/filtering users."""
    search = forms.CharField(
        required=False,
        widget=forms.TextInput(attrs={
            'placeholder': 'Search by username, email, or name...',
            'class': 'w-full px-3 py-2 border border-gray-300 rounded-md'
        })
    )
    status = forms.ChoiceField(
        choices=[
            ('all', 'All Users'),
            ('active', 'Active'),
            ('inactive', 'Inactive'),
        ],
        required=False,
        initial='active'
    )
    role = forms.ModelChoiceField(
        queryset=Group.objects.all(),
        required=False,
        empty_label="All Roles"
    )
