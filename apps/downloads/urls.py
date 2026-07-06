from django.urls import path

from .views import import_detail, import_detail_fragment, import_list, item_download, item_query, item_search, item_transfer, process_round, refresh_status

urlpatterns = [
    path("", import_list, name="downloads-import-list"),
    path("<int:pk>/", import_detail, name="downloads-import-detail"),
    path("<int:pk>/fragment/", import_detail_fragment, name="downloads-import-detail-fragment"),
    path("<int:pk>/processar/", process_round, name="downloads-process-round"),
    path("<int:pk>/atualizar-status/", refresh_status, name="downloads-refresh-status"),
    path("<int:pk>/itens/<int:item_pk>/query/", item_query, name="downloads-item-query"),
    path("<int:pk>/itens/<int:item_pk>/buscar/", item_search, name="downloads-item-search"),
    path("<int:pk>/itens/<int:item_pk>/transferir/", item_transfer, name="downloads-item-transfer"),
    path("<int:pk>/itens/<int:item_pk>/baixar/", item_download, name="downloads-item-download"),
]
