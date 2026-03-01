from django.db import models


class WantedSource(models.Model):
    """Where wanted items come from — blogs, playlists, manual entry, etc."""

    SOURCE_TYPES = [
        ('manual', 'Manual'),
        ('blog', 'Blog'),
        ('spotify', 'Spotify'),
        ('soundcloud', 'SoundCloud'),
        ('youtube', 'YouTube'),
        ('telegram', 'Telegram'),
        ('discogs', 'Discogs'),
    ]

    name = models.CharField(max_length=255)
    url = models.URLField(blank=True)
    source_type = models.CharField(max_length=50, choices=SOURCE_TYPES, default='manual')
    last_checked = models.DateTimeField(null=True, blank=True)
    active = models.BooleanField(default=True)
    created = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['name']

    def __str__(self):
        return f"{self.name} ({self.source_type})"


class WantedItem(models.Model):
    """A track the user wants to find and download."""

    STATUS_CHOICES = [
        ('pending', 'Pending'),
        ('identified', 'Identified'),
        ('searching', 'Searching'),
        ('found', 'Found'),
        ('downloading', 'Downloading'),
        ('downloaded', 'Downloaded'),
        ('tagged', 'Tagged'),
        ('organized', 'Organized'),
        ('not_found', 'Not Found'),
        ('failed', 'Failed'),
    ]

    IDENTIFIED_VIA_CHOICES = [
        ('manual', 'Manual'),
        ('acoustid', 'AcoustID'),
        ('shazam', 'Shazam'),
    ]

    artist = models.CharField(max_length=500, blank=True)
    title = models.CharField(max_length=500, blank=True)
    release_name = models.CharField(max_length=500, blank=True, help_text='Album or EP name')
    catalog_number = models.CharField(max_length=100, blank=True, help_text='e.g. WARP123')
    label = models.CharField(max_length=255, blank=True)
    source = models.ForeignKey(
        WantedSource,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='items',
    )
    notes = models.TextField(blank=True, help_text='e.g. "heard in Feb mix at 23:15"')
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='pending')
    identified_via = models.CharField(
        max_length=20,
        choices=IDENTIFIED_VIA_CHOICES,
        null=True,
        blank=True,
    )
    acoustid_fingerprint = models.TextField(null=True, blank=True)
    file_path = models.CharField(max_length=1000, null=True, blank=True)
    error_message = models.TextField(blank=True)

    # Search tracking
    search_count = models.IntegerField(default=0)
    last_searched = models.DateTimeField(null=True, blank=True)
    best_match_score = models.FloatField(null=True, blank=True)

    added = models.DateTimeField(auto_now_add=True)
    updated = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-added']

    def __str__(self):
        parts = []
        if self.artist:
            parts.append(self.artist)
        if self.title:
            parts.append(self.title)
        elif self.release_name:
            parts.append(self.release_name)
        return ' - '.join(parts) or f"WantedItem #{self.pk}"


class ImportOperation(models.Model):
    """Tracks a playlist/wishlist import from an external source."""

    IMPORT_TYPES = [
        ('youtube', 'YouTube'),
        ('soundcloud', 'SoundCloud'),
        ('spotify', 'Spotify'),
        ('discogs', 'Discogs'),
    ]

    STATUS_CHOICES = [
        ('pending', 'Pending'),
        ('fetching', 'Fetching'),
        ('previewing', 'Previewing'),
        ('importing', 'Importing'),
        ('completed', 'Completed'),
        ('failed', 'Failed'),
    ]

    import_type = models.CharField(max_length=20, choices=IMPORT_TYPES)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='pending')
    url = models.URLField(blank=True)
    source = models.ForeignKey(
        WantedSource,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='import_operations',
    )
    preview_data = models.JSONField(default=list, blank=True)
    summary = models.JSONField(default=dict, blank=True)
    total_found = models.IntegerField(default=0)
    duplicates_found = models.IntegerField(default=0)
    items_imported = models.IntegerField(default=0)
    error_message = models.TextField(blank=True)
    created = models.DateTimeField(auto_now_add=True)
    updated = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-created']

    def __str__(self):
        return f"{self.get_import_type_display()} import ({self.status})"
