from django.db import models


class LibraryTrack(models.Model):
    SOURCE_CHOICES = [
        ('organize_pipeline', 'Organize Pipeline'),
        ('traxdb', 'TraxDB'),
        ('manual', 'Manual'),
    ]

    FORMAT_CHOICES = [
        ('mp3', 'MP3'),
        ('flac', 'FLAC'),
        ('aiff', 'AIFF'),
        ('wav', 'WAV'),
        ('ogg', 'OGG'),
        ('m4a', 'M4A'),
    ]

    file_path = models.CharField(max_length=1000, unique=True)
    file_mtime = models.FloatField(default=0, help_text='File modification time for incremental scan')

    # Metadata
    artist = models.CharField(max_length=500, blank=True)
    title = models.CharField(max_length=500, blank=True)
    album = models.CharField(max_length=500, blank=True)
    label = models.CharField(max_length=500, blank=True)
    catalog_number = models.CharField(max_length=100, blank=True)
    genre = models.CharField(max_length=200, blank=True)
    year = models.CharField(max_length=10, blank=True)

    # Technical info
    format = models.CharField(max_length=10, choices=FORMAT_CHOICES, blank=True)
    bitrate = models.IntegerField(null=True, blank=True, help_text='Bitrate in kbps')
    sample_rate = models.IntegerField(null=True, blank=True, help_text='Sample rate in Hz')
    duration_seconds = models.FloatField(null=True, blank=True)
    file_size_bytes = models.BigIntegerField(default=0)
    has_artwork = models.BooleanField(default=False)

    # Tracking
    source = models.CharField(max_length=20, choices=SOURCE_CHOICES, default='organize_pipeline')
    missing = models.BooleanField(default=False, help_text='File no longer exists on disk')
    added_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-added_at']

    def __str__(self):
        if self.artist and self.title:
            return f"{self.artist} - {self.title}"
        return self.file_path.split('/')[-1]
