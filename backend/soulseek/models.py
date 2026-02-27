from django.db import models
from wanted.models import WantedItem


class SearchQueueItem(models.Model):
    """An item in the Soulseek search queue. Independent from WantedItem."""

    STATUS_CHOICES = [
        ('pending', 'Pending'),
        ('searching', 'Searching'),
        ('found', 'Found'),
        ('not_found', 'Not Found'),
        ('downloading', 'Downloading'),
        ('downloaded', 'Downloaded'),
        ('failed', 'Failed'),
    ]

    # Origin tracking (optional)
    wanted_item = models.ForeignKey(
        WantedItem,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='queue_items',
    )

    # Search metadata — copied from WantedItem or entered via free-text
    artist = models.CharField(max_length=500, blank=True)
    title = models.CharField(max_length=500, blank=True)
    release_name = models.CharField(max_length=500, blank=True)
    catalog_number = models.CharField(max_length=100, blank=True)
    label = models.CharField(max_length=500, blank=True)
    raw_query = models.CharField(max_length=1000, blank=True, help_text='Free-text search query')

    # Status
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='pending')
    search_count = models.IntegerField(default=0)
    last_searched = models.DateTimeField(null=True, blank=True)
    best_match_score = models.FloatField(default=0)
    error_message = models.TextField(blank=True)

    created = models.DateTimeField(auto_now_add=True)
    updated = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-updated']

    def __str__(self):
        if self.raw_query:
            return f"[Q] {self.raw_query}"
        parts = []
        if self.artist:
            parts.append(self.artist)
        if self.title:
            parts.append(self.title)
        return ' - '.join(parts) if parts else f"Queue item #{self.pk}"

    @property
    def display_label(self):
        if self.raw_query:
            return self.raw_query
        parts = []
        if self.artist:
            parts.append(self.artist)
        if self.title:
            parts.append(self.title)
        if parts:
            return ' - '.join(parts)
        if self.release_name:
            return self.release_name
        if self.catalog_number:
            return self.catalog_number
        return f"Queue item #{self.pk}"

    @property
    def search_query(self):
        """Build the query string for slskd search."""
        if self.raw_query:
            return self.raw_query
        parts = []
        if self.artist:
            parts.append(self.artist)
        if self.title:
            parts.append(self.title)
        return ' '.join(parts) if parts else ''


class SearchResult(models.Model):
    """A search result from slskd for a queue item."""

    queue_item = models.ForeignKey(
        'SearchQueueItem',
        on_delete=models.CASCADE,
        related_name='search_results',
        null=True,
        blank=True,
    )
    # Keep wanted_item FK for backward compat, but loose coupling now
    wanted_item = models.ForeignKey(
        WantedItem,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='search_results',
    )
    username = models.CharField(max_length=255)
    filename = models.CharField(max_length=1000)
    file_size = models.BigIntegerField(default=0)
    file_extension = models.CharField(max_length=20, blank=True)
    bitrate = models.IntegerField(null=True, blank=True)
    bit_depth = models.IntegerField(null=True, blank=True)
    sample_rate = models.IntegerField(null=True, blank=True)
    length_seconds = models.IntegerField(null=True, blank=True)

    # Matching scores
    match_score = models.FloatField(default=0)
    artist_score = models.FloatField(default=0)
    title_score = models.FloatField(default=0)

    # User stats from slskd
    upload_speed = models.IntegerField(null=True, blank=True)
    queue_length = models.IntegerField(null=True, blank=True)
    free_upload_slots = models.BooleanField(default=False)

    created = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-match_score']

    def __str__(self):
        return f"{self.filename} ({self.match_score:.0f}%)"


class Download(models.Model):
    """Tracks a download from slskd."""

    STATUS_CHOICES = [
        ('queued', 'Queued'),
        ('downloading', 'Downloading'),
        ('completed', 'Completed'),
        ('failed', 'Failed'),
        ('cancelled', 'Cancelled'),
    ]

    queue_item = models.ForeignKey(
        'SearchQueueItem',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='downloads',
    )
    wanted_item = models.ForeignKey(
        WantedItem,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='downloads',
    )
    search_result = models.ForeignKey(
        SearchResult,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
    )
    username = models.CharField(max_length=255)
    filename = models.CharField(max_length=1000)
    local_path = models.CharField(max_length=1000, blank=True)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='queued')
    progress = models.FloatField(default=0)  # 0-100
    error_message = models.TextField(blank=True)

    # slskd tracking
    slskd_id = models.CharField(max_length=255, blank=True, help_text='slskd transfer ID')

    started = models.DateTimeField(auto_now_add=True)
    completed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['-started']

    def __str__(self):
        return f"{self.filename} [{self.status}]"


class QualityPreset(models.Model):
    """Quality preferences for downloading."""

    name = models.CharField(max_length=100, unique=True)
    preferred_formats = models.JSONField(
        default=list,
        help_text='Ordered list: ["flac", "wav", "aiff", "mp3"]',
    )
    min_bitrate = models.IntegerField(default=256, help_text='Minimum kbps for lossy')
    max_file_size_mb = models.IntegerField(default=200)
    min_file_size_mb = models.IntegerField(default=2)
    is_default = models.BooleanField(default=False)

    class Meta:
        ordering = ['name']

    def __str__(self):
        return self.name

    def save(self, *args, **kwargs):
        # Ensure only one default
        if self.is_default:
            QualityPreset.objects.filter(is_default=True).exclude(pk=self.pk).update(is_default=False)
        super().save(*args, **kwargs)
