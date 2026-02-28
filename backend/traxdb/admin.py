from django.contrib import admin
from .models import TraxDBOperation


@admin.register(TraxDBOperation)
class TraxDBOperationAdmin(admin.ModelAdmin):
    list_display = ['op_type', 'status', 'created', 'updated']
    list_filter = ['op_type', 'status']
    readonly_fields = ['summary', 'report_path', 'progress_path']
