from django.db import models


class Config(models.Model):
    """Key-value config store for app-wide settings."""
    key = models.CharField(max_length=255, unique=True)
    value = models.TextField(blank=True)
    updated = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['key']

    def __str__(self):
        return f"{self.key}={self.value[:50]}"
