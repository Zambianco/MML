from pathlib import Path

from django.contrib import messages
from django.db.models import Count
from django.http import HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.http import require_POST

from apps.core.audio import stream_audio_file
from apps.library.models import Album, Artist, Track
from apps.scanner.tasks import scan_monitored_directories_task

from .backup import get_backup_control, pending_backup_queryset, pause_backup_control, reconcile_backup_manifest, resume_backup_control
from .forms import BackupReconciliationForm, BackupTargetForm, MonitoredDirectoryForm
from .models import BackupTarget, MediaFile, MonitoredDirectory
from .services import cleanup_ready_queryset, preferred_media_file_for_track
from .tasks import delete_local_flac_task, enqueue_pending_transcodes, start_backup_batch


def library_dashboard(request: HttpRequest) -> HttpResponse:
    try:
        enqueue_pending_transcodes(limit=25)
    except Exception:
        pass

    form = MonitoredDirectoryForm()
    backup_target_form = BackupTargetForm()
    backup_reconcile_form = BackupReconciliationForm()
    backup_control = get_backup_control()
    directories = MonitoredDirectory.objects.all()
    backup_targets = BackupTarget.objects.all()
    recent_tracks = Track.objects.select_related("artist", "album").annotate(file_count=Count("media_files")).order_by(
        "-updated_at"
    )[:12]
    recent_imports = MediaFile.objects.select_related("track", "track__artist", "track__album").order_by("-updated_at")[:12]
    pending_backups = pending_backup_queryset().select_related("track", "track__artist")[:12]
    cleanup_candidates = cleanup_ready_queryset().select_related("track", "track__artist")[:12]
    featured_artists = Artist.objects.annotate(
        track_count=Count("tracks", distinct=True),
        album_count=Count("albums", distinct=True),
    ).order_by("-track_count", "name")[:8]
    context = {
        "form": form,
        "backup_target_form": backup_target_form,
        "backup_reconcile_form": backup_reconcile_form,
        "backup_control": backup_control,
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
        "pending_backups": pending_backups,
        "cleanup_candidates": cleanup_candidates,
        "backup_pending_count": pending_backup_queryset().count(),
        "backup_confirmed_count": MediaFile.objects.filter(original_backup_status=MediaFile.BackupStatus.CONFIRMED).count(),
        "backup_failed_count": MediaFile.objects.filter(original_backup_status=MediaFile.BackupStatus.FAILED).count(),
        "transcode_pending_count": MediaFile.objects.filter(transcode_status=MediaFile.TranscodeStatus.PENDING).count(),
        "transcode_processing_count": MediaFile.objects.filter(transcode_status=MediaFile.TranscodeStatus.PROCESSING).count(),
        "transcode_completed_count": MediaFile.objects.filter(transcode_status=MediaFile.TranscodeStatus.COMPLETED).count(),
        "transcode_failed_count": MediaFile.objects.filter(transcode_status=MediaFile.TranscodeStatus.FAILED).count(),
        "cleanup_ready_count": cleanup_ready_queryset().count(),
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
    try:
        scan_monitored_directories_task.delay()
        messages.success(request, "Atualizacao do acervo enviada para o Celery.")
    except Exception:
        messages.error(request, "Nao foi possivel enviar a atualizacao do acervo para o Celery.")
    return redirect(reverse("library-dashboard"))


@require_POST
def reconcile_backups(request: HttpRequest) -> HttpResponse:
    form = BackupReconciliationForm(request.POST)
    if not form.is_valid():
        messages.error(request, "Nao foi possivel ler o manifesto de backup. Verifique o conteudo enviado.")
        return redirect(reverse("library-dashboard"))

    if get_backup_control().is_running:
        messages.info(request, "Backup em andamento. Aguarde terminar ou pause antes de reenviar o manifesto.")
        return redirect(reverse("library-dashboard"))

    try:
        result = reconcile_backup_manifest(manifest_text=form.cleaned_data["manifest_text"])
    except Exception:
        messages.error(request, "Nao foi possivel analisar o manifesto de backup.")
        return redirect(reverse("library-dashboard"))

    if result.missing_media_files:
        start_backup_batch(media_file_ids=[media_file.id for media_file in result.missing_media_files])
        messages.success(request, f"{len(result.missing_media_files)} backup(s) reenviados a partir do manifesto enviado.")
    else:
        messages.warning(request, "Nenhum arquivo faltante foi encontrado no manifesto enviado.")

    if result.unavailable_media_files:
        messages.warning(
            request,
            f"{len(result.unavailable_media_files)} arquivo(s) faltante(s) nao estao mais acessiveis localmente e nao puderam ser reenviados.",
        )
    return redirect(reverse("library-dashboard"))


@require_POST
def queue_backups(request: HttpRequest) -> HttpResponse:
    control = get_backup_control()
    if control.is_running:
        if control.is_paused:
            resume_backup_control()
            messages.success(request, "Backup retomado. O upload atual vai terminar antes de seguir com a proxima faixa.")
        else:
            messages.info(request, "Backup ja esta em andamento.")
        return redirect(reverse("library-dashboard"))

    if not pending_backup_queryset().exists():
        messages.warning(request, "Nenhum backup pendente foi encontrado.")
        return redirect(reverse("library-dashboard"))

    start_backup_batch()
    messages.success(request, "Backup enviado para o Celery.")
    return redirect(reverse("library-dashboard"))


@require_POST
def pause_backups(request: HttpRequest) -> HttpResponse:
    control = pause_backup_control()
    if control.is_running:
        messages.success(request, "Backup pausado. O upload atual sera concluido antes da pausa entrar em vigor.")
    else:
        messages.info(request, "Backup pausado.")
    return redirect(reverse("library-dashboard"))


@require_POST
def queue_cleanup(request: HttpRequest) -> HttpResponse:
    queued = 0
    for media_file in cleanup_ready_queryset():
        try:
            delete_local_flac_task.delay(media_file.id)
            queued += 1
        except Exception:
            pass
    if queued:
        messages.success(request, f"{queued} exclusao(oes) locais enviadas para o Celery.")
    else:
        messages.warning(request, "Nenhum FLAC elegivel foi enviado para exclusao.")
    return redirect(reverse("library-dashboard"))


def media_file_stream(request: HttpRequest, pk: int) -> HttpResponse:
    media_file = get_object_or_404(MediaFile, pk=pk)
    preferred = preferred_media_file_for_track(media_file.track_id) if media_file.track_id else None
    file_path = Path((preferred.source_path or preferred.path) if preferred else (media_file.source_path or media_file.path))
    if not file_path.is_file():
        return HttpResponse(status=404)
    return stream_audio_file(request, file_path)
