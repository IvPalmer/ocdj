"""Cratemate models.

Three append-only tables — `AlbumIdentification` (raw input + initial
identification), `IdentifiedRelease` (enriched metadata pulled from Discogs +
Spotify + Bandcamp + YouTube), `RecognitionRun` (audit log linking input → result).

Kept deliberately simple — no FK to `auth.User` yet, since the V1 module sits
behind Cloudflare Access and treats every authenticated request as the operator.
Add user attribution in V2 when the public-readable variant lands.
"""
from django.db import models


class AlbumIdentification(models.Model):
    """A single image upload + the raw vision-LM (or fallback) response."""

    METHOD_CHOICES = [
        ('claude_vision', 'Claude vision (Max OAuth)'),
        ('gemini', 'Gemini Vision (legacy V1)'),
        ('vision_ocr', 'Google Vision OCR'),
        ('universal', 'Universal CLIP search'),
        ('manual', 'Manual artist+album entry'),
    ]

    image_hash = models.CharField(max_length=64, db_index=True, blank=True)
    method = models.CharField(max_length=20, choices=METHOD_CHOICES, default='claude_vision')
    raw_response = models.JSONField(default=dict, blank=True)
    artist_guess = models.CharField(max_length=500, blank=True)
    album_guess = models.CharField(max_length=500, blank=True)
    confidence = models.FloatField(null=True, blank=True)
    error_message = models.TextField(blank=True)
    created = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created']

    def __str__(self):
        return f"{self.artist_guess or '?'} - {self.album_guess or '?'} ({self.method})"


class IdentifiedRelease(models.Model):
    """Enriched metadata for an identified album — joined from multiple sources."""

    identification = models.ForeignKey(
        AlbumIdentification, on_delete=models.CASCADE, related_name='releases'
    )
    artist = models.CharField(max_length=500)
    album = models.CharField(max_length=500)
    release_date = models.CharField(max_length=50, blank=True)
    genres = models.JSONField(default=list, blank=True)
    cover_image_url = models.CharField(max_length=2000, blank=True)

    discogs_url = models.CharField(max_length=2000, blank=True)
    spotify_url = models.CharField(max_length=2000, blank=True)
    youtube_url = models.CharField(max_length=2000, blank=True)
    bandcamp_url = models.CharField(max_length=2000, blank=True)

    tracklist = models.JSONField(default=list, blank=True)
    market_stats = models.JSONField(default=dict, blank=True)
    extra = models.JSONField(default=dict, blank=True)

    created = models.DateTimeField(auto_now_add=True)
    updated = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-created']

    def __str__(self):
        return f"{self.artist} - {self.album}"


class RecognitionRun(models.Model):
    """Audit log: each /api/cratemate/identify/ call produces one row.

    Links input identification → enriched release. Useful for debugging which
    code path produced a given user-facing answer.
    """

    STATUS_CHOICES = [
        ('pending', 'Pending'),
        ('running', 'Running'),
        ('completed', 'Completed'),
        ('failed', 'Failed'),
    ]

    identification = models.ForeignKey(
        AlbumIdentification, on_delete=models.SET_NULL, null=True, blank=True, related_name='runs'
    )
    release = models.ForeignKey(
        IdentifiedRelease, on_delete=models.SET_NULL, null=True, blank=True, related_name='runs'
    )
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='pending')
    duration_ms = models.IntegerField(null=True, blank=True)
    sources_used = models.JSONField(default=list, blank=True)
    error_message = models.TextField(blank=True)
    created = models.DateTimeField(auto_now_add=True)
    updated = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-created']

    def __str__(self):
        return f"Run {self.id} ({self.status})"
