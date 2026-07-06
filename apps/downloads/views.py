from django.contrib import messages
from django.core.exceptions import ValidationError
from django.http import HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.http import require_http_methods

from .forms import TrackImportUploadForm
from .models import TrackImport
from .services import create_track_import


@require_http_methods(["GET", "POST"])
def import_list(request: HttpRequest) -> HttpResponse:
    form = TrackImportUploadForm()
    if request.method == "POST":
        form = TrackImportUploadForm(request.POST, request.FILES)
        if form.is_valid():
            try:
                track_import = create_track_import(form.cleaned_data["file"])
            except ValidationError as exc:
                form.add_error("file", exc.message)
            else:
                messages.success(request, "CSV importado e fila de busca criada.")
                return redirect(reverse("downloads-import-detail", args=[track_import.pk]))

    imports = TrackImport.objects.all()[:20]
    context = {
        "form": form,
        "imports": imports,
    }
    return render(request, "downloads/import_list.html", context)


def import_detail(request: HttpRequest, pk: int) -> HttpResponse:
    track_import = get_object_or_404(TrackImport.objects.prefetch_related("items"), pk=pk)
    context = {
        "track_import": track_import,
        "items": track_import.items.all(),
    }
    return render(request, "downloads/import_detail.html", context)
