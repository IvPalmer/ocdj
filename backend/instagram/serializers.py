from rest_framework import serializers

COLLECTION_ID = '18117783274346248'


class StrictInput(serializers.Serializer):
    def to_internal_value(self, data):
        if not isinstance(data, dict) or set(data) - set(self.fields):
            raise serializers.ValidationError('Unexpected payload fields')
        return super().to_internal_value(data)


class ChildInput(StrictInput):
    id = serializers.RegexField(r'^\d{1,40}$')
    kind = serializers.ChoiceField(choices=['video', 'image'])


class PostInput(StrictInput):
    id = serializers.RegexField(r'^\d{1,40}$')
    permalink = serializers.RegexField(r'^https://www\.instagram\.com/(?:p|reel)/[A-Za-z0-9_-]{1,64}/$')
    author = serializers.CharField(max_length=150, allow_blank=True, default='')
    caption = serializers.CharField(max_length=10000, allow_blank=True, default='')
    children = ChildInput(many=True, min_length=1, max_length=20)


class SyncInput(StrictInput):
    collection_id = serializers.ChoiceField(choices=[COLLECTION_ID])
    fixture = serializers.BooleanField(default=False)
    posts = PostInput(many=True, min_length=0, max_length=2000)

    def validate(self, data):
        posts = [p['id'] for p in data['posts']]
        children = [c['id'] for p in data['posts'] for c in p['children']]
        if len(posts) != len(set(posts)) or len(children) != len(set(children)):
            raise serializers.ValidationError('Duplicate Instagram IDs')
        return data


class EditInput(StrictInput):
    artist = serializers.CharField(max_length=500, allow_blank=True, required=False)
    title = serializers.CharField(max_length=500, allow_blank=True, required=False)
    reviewed = serializers.BooleanField(required=False)


class Candidate(StrictInput):
    artist = serializers.CharField(max_length=500)
    title = serializers.CharField(max_length=500)


class EvidenceRecord(StrictInput):
    source = serializers.ChoiceField(choices=['caption', 'comment'])
    comment_id = serializers.RegexField(r'^\d{0,40}$', allow_blank=True)
    text = serializers.CharField(max_length=1100)
    candidate = Candidate()


class RecognitionEvidence(StrictInput):
    source = serializers.ChoiceField(choices=['caption', 'comment', 'shazam'])
    certainty = serializers.ChoiceField(choices=['explicit', 'tentative'])
    comments = serializers.ChoiceField(choices=['not-read', 'partial', 'unavailable'])
    outcome = serializers.ChoiceField(choices=['identified', 'no-match', 'error'])
    records = EvidenceRecord(many=True, max_length=10, default=list)


class Completion(StrictInput):
    token = serializers.CharField(max_length=64)
    recognition = RecognitionEvidence(required=False)
    status = serializers.ChoiceField(choices=['identified', 'no-match', 'error'])
    candidates = Candidate(many=True, max_length=10, default=list)

    def validate(self, data):
        if (data['status'] == 'identified') != bool(data['candidates']):
            raise serializers.ValidationError('Identified requires candidates; other results must be empty')
        return data


class ThumbnailInput(StrictInput):
    post_id = serializers.RegexField(r'^\d{1,40}$')
    jpeg = serializers.CharField(max_length=174764, trim_whitespace=False)


class ThumbnailBatch(StrictInput):
    sync_token = serializers.CharField(max_length=512)
    thumbnails = ThumbnailInput(many=True, min_length=1, max_length=16)

    def validate_thumbnails(self, items):
        ids = [item['post_id'] for item in items]
        if len(ids) != len(set(ids)):
            raise serializers.ValidationError('Duplicate post IDs')
        return items
