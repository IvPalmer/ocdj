from django.db import models, transaction


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
        ('skipped', 'Skipped'),
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

    # When the Mac daemon claimed this list. Downloads happen off-box, so a
    # daemon that dies mid-list would strand it in 'downloading' forever; the
    # claim is a lease and a stale one is re-offered.
    claimed_at = models.DateTimeField(null=True, blank=True)
    # Fences the lease: complete/fail must present the token they were handed.
    # Without it a worker whose lease expired can still report an outcome and
    # clobber the result of whoever picked the list up afterwards.
    claim_token = models.CharField(max_length=64, blank=True)

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


class MacInventory(models.Model):
    """What date folders the Mac archive currently holds.

    Downloads land on the Mac, not the VPS, so the backend can no longer scan
    TRAXDB_ROOT to decide what it already has. The Mac daemon reports its
    non-empty date folders here on every poll, and the sync reads this instead
    of the local filesystem.

    Singleton (pk=1) — `report()` is the only writer.
    """

    date_dirs = models.JSONField(default=list, help_text='YYYY-MM-DD folders present on the Mac')
    reported_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name_plural = 'Mac inventory'

    def __str__(self):
        return f"{len(self.date_dirs or [])} date dirs @ {self.reported_at:%Y-%m-%d %H:%M}"

    @classmethod
    def report(cls, date_dirs, *, merge=False):
        """Store the Mac's listing.

        `merge=True` adds to what's already recorded instead of replacing it —
        used when a single completed folder is folded in, so it can't race a
        concurrent full report into dropping dates. The read and the write are
        done under a row lock for the same reason.
        """
        incoming = {str(d).strip() for d in date_dirs if str(d).strip()}
        with transaction.atomic():
            row = cls.objects.select_for_update().filter(pk=1).first()
            if row is None:
                row = cls.objects.create(pk=1, date_dirs=sorted(incoming))
                return row
            if merge:
                incoming |= set(row.date_dirs or [])
            row.date_dirs = sorted(incoming)
            row.save(update_fields=['date_dirs', 'reported_at'])
        return row

    @classmethod
    def current(cls):
        """Stored listing, or [] when the Mac has never reported."""
        row = cls.objects.filter(pk=1).first()
        return list(row.date_dirs or []) if row else []
