#!/bin/sh
set -e

RED='\033[0;31m'
NC='\033[0m'

COMPOSE_FILE="${1:-docker-compose.yml}"
SCRIPT_DIR="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
REPO_DIR="$(CDPATH= cd -- "$SCRIPT_DIR/.." && pwd)"

cd "$REPO_DIR"

# Carrega .env se existir e GIT_USERNAME/GIT_PASSWORD nao estiverem definidos
if [ -f "$REPO_DIR/.env" ]; then
  [ -z "$GIT_USERNAME" ] && GIT_USERNAME="$(grep -m1 '^GIT_USERNAME=' "$REPO_DIR/.env" | cut -d= -f2-)"
  [ -z "$GIT_PASSWORD" ] && GIT_PASSWORD="$(grep -m1 '^GIT_PASSWORD=' "$REPO_DIR/.env" | cut -d= -f2-)"
fi

if [ -z "$GIT_USERNAME" ] || [ -z "$GIT_PASSWORD" ]; then
  printf "${RED}Aviso: GIT_USERNAME ou GIT_PASSWORD nao definidos. O git pode pedir credenciais interativamente.${NC}\n" >&2
fi

if ! command -v docker >/dev/null 2>&1; then
  echo "Erro: docker nao encontrado no PATH." >&2
  exit 1
fi

if ! command -v git >/dev/null 2>&1; then
  echo "Erro: git nao encontrado no PATH." >&2
  exit 1
fi

if [ ! -f "$COMPOSE_FILE" ]; then
  echo "Erro: compose file nao encontrado: $COMPOSE_FILE" >&2
  exit 1
fi

if ! git rev-parse --is-inside-work-tree >/dev/null 2>&1; then
  echo "Erro: diretorio atual nao eh um repositorio git valido." >&2
  exit 1
fi

if ! git diff --quiet --ignore-submodules -- || ! git diff --cached --quiet --ignore-submodules --; then
  echo "Erro: ha alteracoes locais em arquivos rastreados. Commit/stash antes do update." >&2
  exit 1
fi

CURRENT_BRANCH="$(git rev-parse --abbrev-ref HEAD)"
if [ "$CURRENT_BRANCH" = "HEAD" ]; then
  echo "Erro: repositorio em detached HEAD. Troque para um branch antes do update." >&2
  exit 1
fi

recover_stack() {
  echo "Tentando reestabelecer o stack com o codigo local..."
  docker compose -f "$COMPOSE_FILE" up -d --build || true
}

print_failure_details() {
  echo "Status atual dos servicos:" >&2
  docker compose -f "$COMPOSE_FILE" ps >&2 || true
  echo "Ultimos logs do web:" >&2
  docker compose -f "$COMPOSE_FILE" logs web --tail 120 >&2 || true
}

trap 'echo "Interrompido." >&2; exit 130' INT TERM

wait_for_web() {
  echo "Aguardando web (gunicorn) aceitar conexoes..."
  end_time=$(( $(date +%s) + 600 ))
  while [ "$(date +%s)" -lt "$end_time" ]; do
    if timeout 10 docker compose -f "$COMPOSE_FILE" exec -T web python -c "import socket; socket.create_connection(('127.0.0.1', 8000), 2).close()" >/dev/null 2>&1; then
      return 0
    fi
    echo "  ... $(docker compose -f "$COMPOSE_FILE" logs web --tail 1 2>&1 | tail -n 1)"
    sleep 3
  done
  echo "Timeout aguardando o web na porta 8000." >&2
  print_failure_details
  return 1
}

run_or_recover() {
  if ! "$@"; then
    echo "Erro ao executar: $*" >&2
    print_failure_details
    recover_stack
    exit 1
  fi
}

echo "1/4 Backup de banco..."
"$SCRIPT_DIR/backup.sh"

echo "2/4 Atualizando codigo (git pull --ff-only)..."
_ASKPASS_SCRIPT=""
if [ -n "$GIT_USERNAME" ] && [ -n "$GIT_PASSWORD" ]; then
  _ASKPASS_SCRIPT="$(mktemp /tmp/git_askpass.XXXXXX)"
  printf '#!/bin/sh\ncase "$1" in\n  *"Username"*) echo "%s" ;;\n  *"Password"*) echo "%s" ;;\nesac\n' \
    "$GIT_USERNAME" "$GIT_PASSWORD" > "$_ASKPASS_SCRIPT"
  chmod +x "$_ASKPASS_SCRIPT"
  export GIT_ASKPASS="$_ASKPASS_SCRIPT"
fi
export GIT_TERMINAL_PROMPT=0

_GIT_OUT="$(git pull --ff-only 2>&1)" || {
  [ -n "$_ASKPASS_SCRIPT" ] && rm -f "$_ASKPASS_SCRIPT"
  echo "$_GIT_OUT"
  if echo "$_GIT_OUT" | grep -qi "authentication\|invalid.*password\|403\|401\|could not read\|terminal prompts disabled\|Repository not found\|access denied"; then
    printf "${RED}Erro de autenticacao: senha/token invalido ou expirado. Atualize GIT_PASSWORD no .env.${NC}\n" >&2
  fi
  exit 1
}
echo "$_GIT_OUT"
[ -n "$_ASKPASS_SCRIPT" ] && rm -f "$_ASKPASS_SCRIPT"
unset GIT_ASKPASS GIT_TERMINAL_PROMPT

echo "3/4 Subindo stack com build..."
run_or_recover docker compose -f "$COMPOSE_FILE" up -d --build
run_or_recover docker compose -f "$COMPOSE_FILE" exec -T nginx nginx -s reload
run_or_recover wait_for_web

echo "4/4 Status final dos servicos:"
docker compose -f "$COMPOSE_FILE" ps

echo "Update concluido."
