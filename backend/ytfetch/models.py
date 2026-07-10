from django.db import models


class FetchJob(models.Model):
    """A single YouTube audio fetch. yt-dlp downloads the best audio into the
    organize pipeline's 01_downloaded/YouTube/ folder; the pipeline then
    auto-processes it (tag -> rename -> convert -> publish)."""

    STATUS_CHOICES = [
        ('queued', 'Queued'),
        ('fetching', 'Fetching'),
        ('downloaded', 'Downloaded'),
        ('failed', 'Failed'),
    ]

    url = models.URLField(max_length=2000)
    video_id = models.CharField(max_length=32, blank=True)
    title = models.CharField(max_length=500, blank=True)
    uploader = models.CharField(max_length=500, blank=True)

    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='queued')
    error_message = models.TextField(blank=True)
    downloaded_path = models.CharField(max_length=1000, blank=True)

    # Set once the downloaded file has been ingested into the organize pipeline.
    pipeline_item = models.ForeignKey(
        'organize.PipelineItem',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='fetch_jobs',
    )

    created = models.DateTimeField(auto_now_add=True)
    updated = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-id']

    def __str__(self):
        return f"{self.title or self.url} [{self.status}]"
