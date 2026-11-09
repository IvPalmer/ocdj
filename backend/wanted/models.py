from django.db import models, transaction
from django.utils import timezone


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
        ('bandcamp', 'Bandcamp'),
        ('dig', 'Dig'),
        ('shazam', 'Shazam'),
    ]

    name = models.CharField(max_length=255)
    url = models.URLField(blank=True)
    source_type = models.CharField(max_length=50, choices=SOURCE_TYPES, default='manual')
    last_checked = models.DateTimeField(null=True, blank=True)
    # How far a polling source has already read. Free-form because each source
    # counts differently — for the Shazam feed it is the `added_at` of the last
    # playlist item taken, which is what makes "from now on, not the backlog"
    # expressible without keeping a list of every id ever seen.
    cursor = models.CharField(max_length=200, blank=True)
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
    cleanup_isrc = models.CharField(max_length=12, blank=True, editable=False)
    metadata_revision = models.PositiveBigIntegerField(default=0, editable=False)
    metadata_protected = models.BooleanField(default=False, editable=False)
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

    # 30s preview, resolved on demand from iTunes/Deezer and cached here.
    # `preview_checked` is set even when nothing was found: a lot of what this
    # app tracks is promo material no catalogue has, and without recording the
    # miss every render would re-ask the same dead question.
    preview_url = models.URLField(max_length=1000, blank=True)
    preview_provider = models.CharField(max_length=20, blank=True)
    preview_checked = models.DateTimeField(null=True, blank=True)
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

    @classmethod
    def from_db(cls, db, field_names, values):
        instance = super().from_db(db, field_names, values)
        instance._loaded_metadata = {k: v for k, v in zip(field_names, values)
                                     if k in ('artist', 'title', 'cleanup_isrc')}
        return instance

    def refresh_from_db(self, using=None, fields=None, from_queryset=None):
        super().refresh_from_db(using=using, fields=fields, from_queryset=from_queryset)
        self._loaded_metadata = {k: self.__dict__[k] for k in ('artist', 'title', 'cleanup_isrc')
                                 if k in self.__dict__}

    def save(self, *args, **kwargs):
        # Central create boundary includes API/imports/Dig/Shazam/admin and the
        # existing Instagram wishlist without editing the other agent's files.
        # Raw SQL, bulk_create and QuerySet.update deliberately bypass save().
        from .services.cleanup_evidence import clean_isrc
        self.cleanup_isrc = clean_isrc(self.cleanup_isrc)
        with transaction.atomic():
            creating = self._state.adding
            if not creating:
                current = type(self).objects.select_for_update().get(pk=self.pk)
                fields = kwargs.get('update_fields')
                # Existing background callers sometimes save a stale whole
                # instance. Values they never edited must not undo cleanup or
                # a human correction committed since that instance was read.
                if fields is None:
                    loaded = getattr(self, '_loaded_metadata', {})
                    for field, value in loaded.items():
                        if getattr(self, field) == value:
                            setattr(self, field, getattr(current, field))
                metadata_edit = any((fields is None or field in fields)
                                    and getattr(self, field) != getattr(current, field)
                                    for field in ('artist', 'title', 'cleanup_isrc'))
                if metadata_edit or getattr(self, '_protect_metadata', False):
                    self.metadata_protected = True
                    self.metadata_revision = current.metadata_revision + 1
                    if fields is not None:
                        kwargs['update_fields'] = set(fields) | {'metadata_protected', 'metadata_revision'}
                else:
                    self.metadata_protected = current.metadata_protected
                    self.metadata_revision = current.metadata_revision
            super().save(*args, **kwargs)
            if creating:
                from .services.cleanup import capture_wanted
                capture_wanted(self)
        self._protect_metadata = False
        self._loaded_metadata = {k: getattr(self, k) for k in ('artist', 'title', 'cleanup_isrc')}

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
        ('bandcamp', 'Bandcamp'),
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
    playlist_name = models.CharField(max_length=500, blank=True)
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


class MetadataCleanup(models.Model):
    """Private immutable observation + processing receipt; subject deletion retains audit.

    IDs identify occurrences, never canonical recordings. No public write API.
    Snapshot is written only at insertion; attempts/evidence/result are worker-owned.
    """
    subject_key = models.CharField(max_length=200, unique=True)
    wanted_id = models.PositiveBigIntegerField(null=True, blank=True)
    job_id = models.PositiveBigIntegerField(null=True, blank=True)
    snapshot = models.JSONField()
    evidence = models.JSONField(default=dict)
    result = models.JSONField(default=dict)
    status = models.CharField(max_length=20, default='queued')
    attempts = models.PositiveSmallIntegerField(default=0)
    lease = models.UUIDField(null=True)
    available_at = models.DateTimeField(default=timezone.now, db_index=True)
    created = models.DateTimeField(auto_now_add=True)
    updated = models.DateTimeField(auto_now=True)
