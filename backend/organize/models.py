from django.db import models
from soulseek.models import Download
from wanted.models import WantedItem


class PipelineItem(models.Model):
    """Tracks one file through the organize pipeline stages."""

    STAGE_CHOICES = [
        ('downloaded', 'Downloaded'),
        ('tagging', 'Tagging'),
        ('tagged', 'Tagged'),
        ('renaming', 'Renaming'),
        ('renamed', 'Renamed'),
        ('converting', 'Converting'),
        ('converted', 'Converted'),
        ('ready', 'Ready'),
        ('failed', 'Failed'),
    ]

    METADATA_SOURCE_CHOICES = [
        ('file', 'File Tags'),
        ('discogs', 'Discogs'),
        ('musicbrainz', 'MusicBrainz'),
        ('manual', 'Manual'),
    ]

    download = models.ForeignKey(
        Download,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='pipeline_items',
    )
    wanted_item = models.ForeignKey(
        WantedItem,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='pipeline_items',
    )

    original_filename = models.CharField(max_length=1000)
    current_path = models.CharField(max_length=1000)
    final_filename = models.CharField(max_length=1000, blank=True)

    # Metadata
    artist = models.CharField(max_length=500, blank=True)
    title = models.CharField(max_length=500, blank=True)
    album = models.CharField(max_length=500, blank=True)
    label = models.CharField(max_length=500, blank=True)
    catalog_number = models.CharField(max_length=100, blank=True)
    genre = models.CharField(max_length=200, blank=True)
    year = models.CharField(max_length=10, blank=True)
    track_number = models.CharField(max_length=10, blank=True)
    has_artwork = models.BooleanField(default=False)

    # Pipeline state
    stage = models.CharField(max_length=20, choices=STAGE_CHOICES, default='downloaded')
    error_message = models.TextField(blank=True)
    metadata_source = models.CharField(
        max_length=20,
        choices=METADATA_SOURCE_CHOICES,
        blank=True,
    )

    created = models.DateTimeField(auto_now_add=True)
    updated = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-created']

    def __str__(self):
        if self.artist and self.title:
            return f"{self.artist} - {self.title} [{self.stage}]"
        return f"{self.original_filename} [{self.stage}]"
