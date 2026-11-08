from django.db import models


class Collection(models.Model):
    instagram_id = models.CharField(max_length=32, unique=True)
    name = models.CharField(max_length=120, default='Track Downloads')
    synced_at = models.DateTimeField(null=True)
    heartbeat_at = models.DateTimeField(null=True)
    connector_status = models.CharField(max_length=32, default='offline')
    fixture = models.BooleanField(default=False)


class Post(models.Model):
    instagram_id = models.CharField(max_length=64, unique=True)
    collection = models.ForeignKey(Collection, on_delete=models.CASCADE, related_name='posts')
    rank = models.PositiveIntegerField()
    active = models.BooleanField(default=True)
    permalink = models.URLField()
    author = models.CharField(max_length=150, blank=True)
    caption = models.TextField(blank=True)

    class Meta:
        ordering = ['rank', 'pk']


class Media(models.Model):
    instagram_id = models.CharField(max_length=64, unique=True)
    post = models.ForeignKey(Post, on_delete=models.CASCADE, related_name='media')
    position = models.PositiveIntegerField()
    kind = models.CharField(max_length=8)
    active = models.BooleanField(default=True)
    status = models.CharField(max_length=16, default='pending')
    candidates = models.JSONField(default=list)
    artist = models.CharField(max_length=500, blank=True)
    title = models.CharField(max_length=500, blank=True)
    reviewed = models.BooleanField(default=False)
    wanted = models.ForeignKey('wanted.WantedItem', null=True, on_delete=models.SET_NULL)
    recognition = models.JSONField(default=dict)
    playback_requested = models.BooleanField(default=False)
    playback_error = models.BooleanField(default=False)
    queued = models.BooleanField(default=False)
    lease_action = models.CharField(max_length=16, blank=True)
    lease_token = models.CharField(max_length=64, blank=True)
    lease_until = models.DateTimeField(null=True)
    cache_key = models.CharField(max_length=64, blank=True)
    cache_until = models.DateTimeField(null=True)

    class Meta:
        ordering = ['position', 'pk']
