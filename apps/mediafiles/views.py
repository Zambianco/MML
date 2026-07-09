from pathlib import Path

from django.contrib import messages
from django.db.models import Count
from django.http import HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.http import require_POST

from apps.core.audio import stream_audio_file
from apps.library.models import Album, Artist, Track
from apps.scanner.services import scan_monitored_directories

from .forms import BackupTargetForm, MonitoredDirectoryForm
from .models import BackupTarget, MediaFile, MonitoredDirectory


def library_dashboard(request: HttpRequest) -> HttpResponse:
    form = MonitoredDirectoryForm()
    backup_target_form = BackupTargetForm()
    directories = MonitoredDirectory.objects.all()
    backup_targets = BackupTarget.objects.all()
    recent_tracks = Track.objects.select_related("artist", "album").annotate(file_count=Count("media_files")).order_by(
        "-updated_at"
    )[:12]
    recent_imports = MediaFile.objects.select_related("track", "track__artist", "track__album").order_by("-updated_at")[:12]
    featured_artists = Artist.objects.annotate(
        track_count=Count("tracks", distinct=True),
        album_count=Count("albums", distinct=True),
    ).order_by("-track_count", "name")[:8]
    context = {
        "form": form,
        "backup_target_form": backup_target_form,
        "backup_targets": backup_targets,
        "directories": directories,
        "directory_count": directories.count(),
        "backup_target_count": backup_targets.count(),
        "media_file_count": MediaFile.objects.count(),
        "artist_count": Artist.objects.count(),
        "album_count": Album.objects.count(),
        "track_count": Track.objects.count(),
        "review_count": MediaFile.objects.filter(needs_review=True).count(),
        "recent_tracks": recent_tracks,
        "recent_imports": recent_imports,
        "featured_artists": featured_artists,
    }
    return render(request, "mediafiles/library_dashboard.html", context)


@require_POST
def create_directory(request: HttpRequest) -> HttpResponse:
    form = MonitoredDirectoryForm(request.POST)
    if form.is_valid():
        form.save()
        messages.success(request, "Diretorio monitorado cadastrado.")
    else:
        messages.error(request, "Nao foi possivel cadastrar o diretorio. Verifique os campos.")
    return redirect(reverse("library-dashboard"))


@require_POST
def create_backup_target(request: HttpRequest) -> HttpResponse:
    form = BackupTargetForm(request.POST)
    if form.is_valid():
        form.save()
        messages.success(request, "Destino de backup cadastrado.")
    else:
        messages.error(request, "Nao foi possivel cadastrar o destino de backup. Verifique os campos.")
    return redirect(reverse("library-dashboard"))


@require_POST
def scan_directories(request: HttpRequest) -> HttpResponse:
    result = scan_monitored_directories()
    messages.success(
        request,
        (
            f"Scan concluido: {result.directories_scanned} diretorios, "
            f"{result.files_seen} arquivos de audio, {result.files_created} novos."
        ),
    )
    if result.missing_directories:
        messages.warning(request, f"{result.missing_directories} diretorio(s) nao encontrado(s).")
    return redirect(reverse("library-dashboard"))


def media_file_stream(request: HttpRequest, pk: int) -> HttpResponse:
    media_file = get_object_or_404(MediaFile, pk=pk)
    file_path = Path(media_file.source_path or media_file.path)
    if not file_path.is_file():
        return HttpResponse(status=404)
    return stream_audio_file(request, file_path)
