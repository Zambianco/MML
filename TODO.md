# TODO

## Streaming de música

- [ ] Servir áudio via nginx com `X-Accel-Redirect` (Django só valida permissão; nginx entrega com Range/sendfile). Montar volume de mídia no nginx e criar `location /protected/ { internal; alias ...; }`.
- [ ] Cache HTTP em `apps/core/audio.py`: `ETag`, `Last-Modified` e `Cache-Control: private, max-age=...` (inclusive no 206).
- [ ] Ajustar proxy do nginx (`docker/nginx/default.conf`): `proxy_buffering off` nas rotas de stream, `sendfile on; tcp_nopush on;`, `proxy_http_version 1.1`, `proxy_read_timeout`.
- [ ] Gunicorn: definir `--worker-class gthread --threads 8` (ou gevent) se o stream continuar no Django.
- [ ] Player (`music_player.html`): `preload="metadata"` e pré-carregar a próxima faixa ~20 s antes do fim (playback sem gap).
- [ ] Transcoding: servir Opus/MP3 no streaming (FLAC só para download); avaliar variante de 64 kbps para mobile.
- [ ] (Opcional) HLS com segmentos de ~10 s, se houver uso fora da rede local.
