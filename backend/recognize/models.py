from django.db import models


class RecognizeJob(models.Model):
    """A mix recognition job — downloads audio and identifies tracks via ShazamIO."""

    STATUS_CHOICES = [
        ('pending', 'Pending'),
        ('downloading', 'Downloading'),
        ('recognizing', 'Recognizing'),
        ('completed', 'Completed'),
        ('failed', 'Failed'),
    ]

    url = models.CharField(max_length=2000)
    title = models.CharField(max_length=500, blank=True)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='pending')
    segments_total = models.IntegerField(default=0)
    segments_done = models.IntegerField(default=0)
    tracks_found = models.IntegerField(default=0)
    tracklist = models.JSONField(default=list, blank=True)
    raw_results = models.JSONField(default=list, blank=True)
    description_tracks = models.JSONField(default=list, blank=True)
    duration_seconds = models.IntegerField(null=True, blank=True)
    engine = models.CharField(max_length=20, default='shazam', blank=True)  # shazam, trackid, hybrid
    error_message = models.TextField(blank=True)
    created = models.DateTimeField(auto_now_add=True)
    updated = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-created']

    def __str__(self):
        return f"{self.title or self.url} ({self.status})"
