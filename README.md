# My Music Library

Nova base do projeto para substituir a arquitetura atual orientada a scripts por um servidor Django modular, executado via Docker Compose.

## Escopo desta etapa

- isolar a nova versão neste repositório;
- definir a topologia inicial de serviços;
- subir um projeto Django com apps separados por responsabilidade;
- expor uma home simples e um healthcheck;
- preparar os pontos de entrada para Celery, PostgreSQL, Redis, Nginx e slskd.

## Estrutura

- `config/`: configuração Django, WSGI, ASGI e Celery.
- `apps/`: apps do domínio.
- `templates/`: base HTML com Bootstrap 5 e HTMX.
- `docker/`: imagens e configuração Nginx.
- `scripts/`: instalação, atualização, backup e restore para Debian.

## Apps iniciais

- `core`: páginas base e healthcheck.
- `accounts`: autenticação e perfis.
- `library`: artistas, obras, gravações, álbuns e lançamentos.
- `mediafiles`: arquivos físicos, bibliotecas e diretórios monitorados.
- `metadata`: MusicBrainz, AcoustID, letras, capas e caches.
- `scanner`: pipeline de ingestão.
- `downloads`: integração com slskd.
- `analytics`: métricas e dashboards.
- `sync`: sincronização incremental.
- `playback`: histórico, favoritos, ratings e bloqueios.
- `playlists`: listas e curadoria.
- `api`: API REST.

## Subida local

```bash
cp .env.example .env
docker compose up --build
```

## Próximo passo natural

Modelar o ER completo em PostgreSQL e começar pelos apps `library` e `mediafiles`, que formam a espinha dorsal do domínio.
