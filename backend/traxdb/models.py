from django.db import models


class TraxDBOperation(models.Model):
    """A single TraxDB operation — sync, download, or audit."""

    OP_TYPE_CHOICES = [
        ('sync', 'Sync'),
        ('download', 'Download'),
        ('audit', 'Audit'),
    ]

    STATUS_CHOICES = [
        ('pending', 'Pending'),
        ('running', 'Running'),
        ('completed', 'Completed'),
        ('failed', 'Failed'),
    ]

    op_type = models.CharField(max_length=20, choices=OP_TYPE_CHOICES)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='pending')

    # Filesystem paths for the JSON reports written by CLI tools
    report_path = models.CharField(max_length=1000, blank=True)
    progress_path = models.CharField(max_length=1000, blank=True)

    # Summary extracted from report on completion
    summary = models.JSONField(default=dict, blank=True)

    error_message = models.TextField(blank=True)

    created = models.DateTimeField(auto_now_add=True)
    updated = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-created']

    def __str__(self):
        return f"[{self.op_type}] {self.status} — {self.created:%Y-%m-%d %H:%M}"
