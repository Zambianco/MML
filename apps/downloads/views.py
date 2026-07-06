from django.contrib import messages
from django.core.exceptions import ValidationError
from django.db.models import Count
from django.http import HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.http import require_http_methods
from urllib.error import URLError

from .forms import TrackImportUploadForm
from .models import TrackImport, TrackImportItem
from .services import (
    create_track_import,
    enqueue_best_available_source,
    process_download_round,
    refresh_auto_item_search_query,
    sync_item_search_query,
    search_slskd_sources,
    update_download_statuses,
)


def _import_detail_context(track_import: TrackImport, *, refresh_status: bool = False) -> dict:
    track_import = TrackImport.objects.prefetch_related("items__sources").get(pk=track_import.pk)
    if refresh_status:
        update_download_statuses(track_import, enqueue_next=False)
        track_import = TrackImport.objects.prefetch_related("items__sources").get(pk=track_import.pk)
    status_counts = dict(track_import.items.values_list("status").annotate(total=Count("id")))
    return {
        "track_import": track_import,
        "items": track_import.items.all(),
        "status_counts": status_counts,
    }


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
    track_import = get_object_or_404(TrackImport, pk=pk)
    return render(request, "downloads/import_detail.html", _import_detail_context(track_import, refresh_status=True))


def import_detail_fragment(request: HttpRequest, pk: int) -> HttpResponse:
    track_import = get_object_or_404(TrackImport, pk=pk)
    return render(request, "downloads/import_detail_fragment.html", _import_detail_context(track_import, refresh_status=True))


@require_http_methods(["POST"])
def process_round(request: HttpRequest, pk: int) -> HttpResponse:
    track_import = get_object_or_404(TrackImport, pk=pk)
    limit = int(request.POST.get("limit") or 0)
    try:
        summary = process_download_round(track_import, limit=limit)
    except URLError as exc:
        messages.error(request, f"Nao foi possivel conectar ao slskd: {exc.reason}")
    else:
        messages.success(
            request,
            f"Rodada concluida: {summary['searched']} busca(s), {summary['queued']} download(s) enfileirado(s).",
        )
    return redirect(reverse("downloads-import-detail", args=[track_import.pk]))


@require_http_methods(["POST"])
def refresh_status(request: HttpRequest, pk: int) -> HttpResponse:
    track_import = get_object_or_404(TrackImport, pk=pk)
    try:
        summary = update_download_statuses(track_import)
    except URLError as exc:
        messages.error(request, f"Nao foi possivel conectar ao slskd: {exc.reason}")
    else:
        messages.success(
            request,
            f"Status atualizado: {summary['updated']} item(ns), {summary['done']} concluido(s), {summary['queued_next']} proxima(s) fonte(s).",
        )
    return redirect(reverse("downloads-import-detail", args=[track_import.pk]))


@require_http_methods(["POST"])
def item_search(request: HttpRequest, pk: int, item_pk: int) -> HttpResponse:
    track_import = get_object_or_404(TrackImport, pk=pk)
    item = get_object_or_404(TrackImportItem, pk=item_pk, track_import=track_import)
    try:
        search_slskd_sources(item)
    except URLError as exc:
        messages.error(request, f"Nao foi possivel conectar ao slskd: {exc.reason}")
    except Exception:
        messages.error(request, "Falha ao buscar fontes no slskd.")
    else:
        messages.success(request, f"Busca concluida para a linha {item.row_number}.")
    return redirect(reverse("downloads-import-detail", args=[track_import.pk]))


@require_http_methods(["POST"])
def item_query(request: HttpRequest, pk: int, item_pk: int) -> HttpResponse:
    track_import = get_object_or_404(TrackImport, pk=pk)
    item = get_object_or_404(TrackImportItem, pk=item_pk, track_import=track_import)
    action = str(request.POST.get("query_action") or "save")
    if action == "regenerate":
        refresh_auto_item_search_query(item)
        messages.success(request, f"Query regenerada na linha {item.row_number}.")
    else:
        search_query = str(request.POST.get("search_query") or "").strip()
        if not search_query:
            messages.error(request, "Informe uma query.")
        else:
            item.search_query_mode = TrackImportItem.SEARCH_QUERY_MANUAL
            item.search_query = search_query
            item.save(update_fields=["search_query", "search_query_mode", "updated_at"])
            messages.success(request, f"Query atualizada na linha {item.row_number}.")
    return redirect(reverse("downloads-import-detail", args=[track_import.pk]))


@require_http_methods(["POST"])
def item_download(request: HttpRequest, pk: int, item_pk: int) -> HttpResponse:
    track_import = get_object_or_404(TrackImport, pk=pk)
    item = get_object_or_404(TrackImportItem, pk=item_pk, track_import=track_import)
    try:
        source = enqueue_best_available_source(item)
    except URLError as exc:
        messages.error(request, f"Nao foi possivel conectar ao slskd: {exc.reason}")
    except ValueError as exc:
        messages.error(request, str(exc))
    except Exception:
        messages.error(request, "Falha ao enfileirar download no slskd.")
    else:
        messages.success(request, f"Download enfileirado para a linha {item.row_number}: {source.username}.")
    return redirect(reverse("downloads-import-detail", args=[track_import.pk]))
