from django.db import models


class TraxDBOperation(models.Model):
    """A single TraxDB operation -- sync, download, or audit."""

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
        return f"[{self.op_type}] {self.status} -- {self.created:%Y-%m-%d %H:%M}"


class ScrapedFolder(models.Model):
    """A Pixeldrain list found by scraping the blog."""

    DOWNLOAD_STATUS_CHOICES = [
        ('pending', 'Pending'),
        ('downloading', 'Downloading'),
        ('downloaded', 'Downloaded'),
        ('failed', 'Failed'),
    ]

    folder_id = models.CharField(max_length=100, unique=True, help_text='Pixeldrain list ID')
    title = models.CharField(max_length=500, blank=True)
    url = models.URLField(max_length=2000, blank=True, help_text='Blog post URL where this list was found')
    pixeldrain_url = models.URLField(max_length=2000, blank=True)
    inferred_date = models.CharField(max_length=10, blank=True, help_text='YYYY-MM-DD inferred from blog post')
    pixeldrain_links = models.JSONField(default=list, blank=True, help_text='List of Pixeldrain URLs found in the post')

    scraped_at = models.DateTimeField(auto_now_add=True)
    download_status = models.CharField(max_length=20, choices=DOWNLOAD_STATUS_CHOICES, default='pending')

    # Track which sync operation discovered this folder
    sync_operation = models.ForeignKey(
        TraxDBOperation, on_delete=models.SET_NULL, null=True, blank=True, related_name='scraped_folders'
    )

    class Meta:
        ordering = ['-scraped_at']

    def __str__(self):
        return f"{self.folder_id} ({self.inferred_date or 'no date'})"


class ScrapedTrack(models.Model):
    """A single file within a Pixeldrain list."""

    DOWNLOAD_STATUS_CHOICES = [
        ('pending', 'Pending'),
        ('downloading', 'Downloading'),
        ('downloaded', 'Downloaded'),
        ('failed', 'Failed'),
    ]

    folder = models.ForeignKey(ScrapedFolder, on_delete=models.CASCADE, related_name='tracks')
    filename = models.CharField(max_length=500)
    pixeldrain_file_id = models.CharField(max_length=100, blank=True)
    pixeldrain_url = models.URLField(max_length=2000, blank=True)
    local_path = models.CharField(max_length=1000, blank=True)
    file_size_bytes = models.BigIntegerField(null=True, blank=True)
    downloaded = models.BooleanField(default=False)
    download_status = models.CharField(max_length=20, choices=DOWNLOAD_STATUS_CHOICES, default='pending')

    class Meta:
        ordering = ['filename']

    def __str__(self):
        return f"{self.filename} ({self.folder.folder_id})"
