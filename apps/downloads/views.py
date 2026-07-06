from pathlib import Path

from django.contrib import messages
from django.core.exceptions import ValidationError
from django.core.paginator import Paginator
from django.db.models import Count
from django.http import FileResponse, HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.http import require_http_methods
from django.conf import settings
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

ITEMS_PER_PAGE = 25


def _import_detail_url(track_import: TrackImport, *, page: str | None = None) -> str:
    url = reverse("downloads-import-detail", args=[track_import.pk])
    return f"{url}?page={page}" if page else url


def _item_page_url(track_import: TrackImport, item: TrackImportItem) -> str:
    page = None
    if item.row_number > 0:
        page = str(((item.row_number - 1) // ITEMS_PER_PAGE) + 1)
    return _import_detail_url(track_import, page=page)


def _resolve_local_download_path(download_path: str) -> Path | None:
    if not download_path.strip():
        return None
    path = Path(download_path)
    roots = [Path(settings.SLSKD_DOWNLOADS_DIR), Path(settings.MUSIC_STORAGE_ROOT)]
    if path.is_absolute():
        try:
            resolved = path.resolve(strict=True)
        except FileNotFoundError:
            resolved = None
        else:
            if resolved.is_file():
                return resolved
        candidates = []
    else:
        candidates = [root / path for root in roots]
    for candidate in candidates:
        try:
            resolved = candidate.resolve(strict=True)
        except FileNotFoundError:
            continue
        if not resolved.is_file():
            continue
        for root in roots:
            resolved_root = root.resolve()
            if resolved_root in resolved.parents or resolved == resolved_root:
                return resolved
    filename = path.name
    for root in roots:
        if not root.exists():
            continue
        for candidate in root.rglob(filename):
            if candidate.is_file():
                return candidate.resolve()
    return None


def _import_detail_context(track_import: TrackImport, *, page: str | None = None, refresh_status: bool = False) -> dict:
    track_import = TrackImport.objects.prefetch_related("items__sources").get(pk=track_import.pk)
    if refresh_status:
        update_download_statuses(track_import, enqueue_next=False)
        track_import = TrackImport.objects.prefetch_related("items__sources").get(pk=track_import.pk)
    status_counts = dict(track_import.items.values_list("status").annotate(total=Count("id")))
    items = track_import.items.prefetch_related("sources").all()
    page_obj = Paginator(items, ITEMS_PER_PAGE).get_page(page)
    return {
        "track_import": track_import,
        "items": page_obj,
        "page_obj": page_obj,
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
    return render(
        request,
        "downloads/import_detail.html",
        _import_detail_context(track_import, page=request.GET.get("page"), refresh_status=True),
    )


def import_detail_fragment(request: HttpRequest, pk: int) -> HttpResponse:
    track_import = get_object_or_404(TrackImport, pk=pk)
    return render(
        request,
        "downloads/import_detail_fragment.html",
        _import_detail_context(track_import, page=request.GET.get("page"), refresh_status=True),
    )


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
    return redirect(_import_detail_url(track_import, page=request.GET.get("page")))


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
    return redirect(_import_detail_url(track_import, page=request.GET.get("page")))


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
    return redirect(_import_detail_url(track_import, page=request.GET.get("page")))


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
    return redirect(_import_detail_url(track_import, page=request.GET.get("page")))


@require_http_methods(["POST"])
def item_transfer(request: HttpRequest, pk: int, item_pk: int) -> HttpResponse:
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
    return redirect(_item_page_url(track_import, item))


def item_download(request: HttpRequest, pk: int, item_pk: int) -> HttpResponse:
    track_import = get_object_or_404(TrackImport, pk=pk)
    item = get_object_or_404(TrackImportItem, pk=item_pk, track_import=track_import)
    download_reference = item.download_path.strip()
    if not download_reference and item.status == TrackImportItem.STATUS_DONE:
        source = item.sources.order_by("rank", "-score").first()
        download_reference = source.remote_filename if source else ""
    file_path = _resolve_local_download_path(download_reference)
    if file_path is None:
        messages.error(request, "Arquivo ainda nao esta disponivel no disco do sistema.")
        return redirect(_item_page_url(track_import, item))
    return FileResponse(file_path.open("rb"), as_attachment=True, filename=file_path.name)
