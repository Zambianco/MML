from django.contrib import messages
from django.http import HttpRequest, HttpResponse
from django.shortcuts import redirect, render
from django.urls import reverse
from django.views.decorators.http import require_POST

from apps.scanner.services import scan_monitored_directories

from .forms import MonitoredDirectoryForm
from .models import MediaFile, MonitoredDirectory


def library_dashboard(request: HttpRequest) -> HttpResponse:
    form = MonitoredDirectoryForm()
    directories = MonitoredDirectory.objects.all()
    media_files = MediaFile.objects.select_related("directory", "track", "track__artist")[:100]
    context = {
        "form": form,
        "directories": directories,
        "media_files": media_files,
        "directory_count": directories.count(),
        "media_file_count": MediaFile.objects.count(),
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
