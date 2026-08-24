from django.apps import AppConfig


class AccountsStubConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'accounts_stub'

    def ready(self):
        import accounts_stub.signals  # noqa: F401
