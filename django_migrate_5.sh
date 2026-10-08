#!/usr/bin/env bash
set -Eeuo pipefail

# Migração segura Django 4.2.x -> 5.0.x
# Uso:
#   ./django_migrate_5.sh migrate [caminho_do_projeto]
#   ./django_migrate_5.sh restore <diretório_do_backup> [caminho_do_projeto]
#   ./django_migrate_5.sh check [caminho_do_projeto]
#
# Variáveis opcionais:
#   PYTHON_BIN=python3.10
#   DJANGO_TARGET=5.0.14   # fixe a versão 5.0.x desejada
#   DATABASE_URL=...       # opcional; caso contrário usa settings do Django
#   REQUIREMENTS_FILE=requirements.txt
#   SETTINGS_MODULE=config.settings

MODE="${1:-}"
if [[ "$MODE" == "restore" ]]; then
  BACKUP_DIR="${2:-}"
  PROJECT_DIR="${3:-$PWD}"
else
  PROJECT_DIR="${2:-$PWD}"
  BACKUP_DIR="${3:-}"
fi
PYTHON_BIN="${PYTHON_BIN:-python3.10}"
DJANGO_TARGET="${DJANGO_TARGET:-5.0.14}"
REQUIREMENTS_FILE="${REQUIREMENTS_FILE:-requirements.txt}"
SETTINGS_MODULE="${SETTINGS_MODULE:-}"
TIMESTAMP="$(date +%Y%m%d_%H%M%S)"
BACKUP_ROOT="${PROJECT_DIR}/.django_migration_backups/${TIMESTAMP}"
VENV_DIR="${PROJECT_DIR}/.venv"

log()  { printf '\n[%s] %s\n' "$(date '+%F %T')" "$*"; }
warn() { printf '\n[AVISO] %s\n' "$*" >&2; }
fatal(){ printf '\n[ERRO] %s\n' "$*" >&2; exit 1; }

need_cmd() { command -v "$1" >/dev/null 2>&1 || fatal "Comando não encontrado: $1"; }

cd_project() {
  [[ -d "$PROJECT_DIR" ]] || fatal "Diretório inexistente: $PROJECT_DIR"
  cd "$PROJECT_DIR"
  [[ -f manage.py ]] || fatal "manage.py não encontrado em $PROJECT_DIR"
}

python_run() {
  if [[ -x "$VENV_DIR/bin/python" ]]; then "$VENV_DIR/bin/python" "$@"; else "$PYTHON_BIN" "$@"; fi
}

pip_run() {
  if [[ -x "$VENV_DIR/bin/python" ]]; then "$VENV_DIR/bin/python" -m pip "$@"; else "$PYTHON_BIN" -m pip "$@"; fi
}

check_requirements() {
  need_cmd git
  need_cmd "$PYTHON_BIN"
  python_run -c 'import sys; assert sys.version_info >= (3,10), sys.version'
  python_run -c 'import django; print("Django instalado:", django.get_version())' 2>/dev/null || true
  [[ -f "$REQUIREMENTS_FILE" ]] || warn "$REQUIREMENTS_FILE não encontrado; será criado pelo procedimento."
}

set_settings_module() {
  if [[ -z "$SETTINGS_MODULE" ]]; then
    SETTINGS_MODULE="$(python_run - <<'PY'
import os, re
p='manage.py'
s=open(p, encoding='utf-8').read()
m=re.search(r'DJANGO_SETTINGS_MODULE[\'\"]\s*,\s*[\'\"]([^\'\"]+)', s)
print(m.group(1) if m else '')
PY
)"
  fi
  [[ -n "$SETTINGS_MODULE" ]] || fatal "Não foi possível descobrir SETTINGS_MODULE. Defina SETTINGS_MODULE=config.settings."
  export DJANGO_SETTINGS_MODULE="$SETTINGS_MODULE"
}

git_backup() {
  mkdir -p "$BACKUP_ROOT"
  git rev-parse --is-inside-work-tree >/dev/null 2>&1 || fatal "O projeto precisa estar em um repositório Git."
  [[ -z "$(git status --porcelain)" ]] || fatal "Existem alterações não commitadas. Faça commit/stash antes de continuar."
  local branch tag
  branch="$(git branch --show-current)"
  tag="django-pre-5-${TIMESTAMP}"
  git tag "$tag"
  git rev-parse HEAD > "$BACKUP_ROOT/git_commit.txt"
  printf '%s\n' "$branch" > "$BACKUP_ROOT/git_branch.txt"
  printf '%s\n' "$tag" > "$BACKUP_ROOT/git_tag.txt"
  git bundle create "$BACKUP_ROOT/repository.bundle" --all >/dev/null
  git ls-files -z | tar --null -T - -czf "$BACKUP_ROOT/tracked_files.tar.gz"
  log "Backup Git criado: $BACKUP_ROOT"
}

settings_dump() {
  set_settings_module
  python_run - <<'PY' > "$BACKUP_ROOT/runtime_settings.txt"
import os
os.environ.setdefault('DJANGO_SETTINGS_MODULE', os.environ['DJANGO_SETTINGS_MODULE'])
import django
django.setup()
from django.conf import settings
for key in ('DATABASES','INSTALLED_APPS','MIDDLEWARE','SECRET_KEY','ROOT_URLCONF'):
    value = getattr(settings, key, None)
    if key == 'SECRET_KEY': value = '<ocultado>'
    print(f'{key}={value!r}')
PY
}

database_backup() {
  set_settings_module
  local engine name user host port dbfile
  engine="$(python_run - <<'PY'
import os, django
django.setup()
from django.conf import settings
print(settings.DATABASES['default']['ENGINE'])
PY
)"
  mkdir -p "$BACKUP_ROOT/database"
  case "$engine" in
    django.db.backends.sqlite3)
      dbfile="$(python_run - <<'PY'
import os, django
django.setup()
from django.conf import settings
print(settings.DATABASES['default']['NAME'])
PY
)"
      [[ -f "$dbfile" ]] || fatal "Banco SQLite não encontrado: $dbfile"
      cp -a "$dbfile" "$BACKUP_ROOT/database/db.sqlite3"
      printf 'sqlite:%s\n' "$dbfile" > "$BACKUP_ROOT/database/metadata.txt"
      ;;
    django.db.backends.postgresql*)
      need_cmd pg_dump
      python_run - <<'PY' > /tmp/django_db_env
import django
django.setup()
from django.conf import settings
c=settings.DATABASES['default']; print(c.get('NAME','')); print(c.get('USER','')); print(c.get('HOST') or ''); print(c.get('PORT') or '')
PY
      mapfile -t v < /tmp/django_db_env
      pg_dump --format=custom --file="$BACKUP_ROOT/database/postgresql.dump" --dbname="${v[0]}" ${v[1]:+--username="${v[1]}"} ${v[2]:+--host="${v[2]}"} ${v[3]:+--port="${v[3]}"}
      ;;
    django.db.backends.mysql)
      need_cmd mysqldump
      python_run - <<'PY' > /tmp/django_db_env
import django
django.setup()
from django.conf import settings
c=settings.DATABASES['default']; print(c.get('NAME','')); print(c.get('USER','')); print(c.get('HOST') or ''); print(c.get('PORT') or '')
PY
      mapfile -t v < /tmp/django_db_env
      mysqldump --single-transaction --routines --triggers --databases "${v[0]}" ${v[1]:+--user="${v[1]}"} ${v[2]:+--host="${v[2]}"} ${v[3]:+--port="${v[3]}"} > "$BACKUP_ROOT/database/mysql.sql"
      ;;
    *) warn "Engine $engine não suportado automaticamente. Faça backup manual do banco antes de prosseguir.";;
  esac
  printf '%s\n' "$engine" > "$BACKUP_ROOT/database/engine.txt"
}

freeze_dependencies() {
  [[ -f "$REQUIREMENTS_FILE" ]] || : > "$REQUIREMENTS_FILE"
  cp -a "$REQUIREMENTS_FILE" "$BACKUP_ROOT/requirements.before.txt"
  python_run -m pip freeze > "$BACKUP_ROOT/pip-freeze.before.txt"
}

run_checks() {
  set_settings_module
  log "Executando checks do Django"
  python_run manage.py check --deploy
  log "Verificando migrações pendentes"
  python_run manage.py makemigrations --check --dry-run
  log "Executando testes"
  python_run manage.py test
}

migrate() {
  cd_project; check_requirements
  git_backup; settings_dump; database_backup; freeze_dependencies
  cp -a "$VENV_DIR" "$BACKUP_ROOT/venv.before" 2>/dev/null || true
  run_checks || fatal "Os checks iniciais falharam. Nada foi atualizado. Backup: $BACKUP_ROOT"
  log "Criando ambiente virtual isolado para a migração"
  "$PYTHON_BIN" -m venv "$BACKUP_ROOT/venv.migration"
  "$BACKUP_ROOT/venv.migration/bin/python" -m pip install --upgrade pip wheel
  if [[ -s "$REQUIREMENTS_FILE" ]]; then "$BACKUP_ROOT/venv.migration/bin/python" -m pip install -r "$REQUIREMENTS_FILE"; fi
  "$BACKUP_ROOT/venv.migration/bin/python" -m pip install "Django==${DJANGO_TARGET}"
  log "Validando compatibilidade em ambiente isolado"
  "$BACKUP_ROOT/venv.migration/bin/python" manage.py check
  "$BACKUP_ROOT/venv.migration/bin/python" manage.py makemigrations --check --dry-run
  "$BACKUP_ROOT/venv.migration/bin/python" manage.py test
  "$BACKUP_ROOT/venv.migration/bin/python" manage.py migrate --plan > "$BACKUP_ROOT/migration_plan.txt"
  "$BACKUP_ROOT/venv.migration/bin/python" manage.py migrate
  "$BACKUP_ROOT/venv.migration/bin/python" -m pip freeze > "$BACKUP_ROOT/pip-freeze.after.txt"
  printf 'BACKUP_ROOT=%s\nDJANGO_TARGET=%s\n' "$BACKUP_ROOT" "$DJANGO_TARGET" > "$BACKUP_ROOT/manifest.txt"
  log "Migração concluída no ambiente isolado. Backup: $BACKUP_ROOT"
  log "Para ativar no ambiente principal, instale Django==${DJANGO_TARGET} e dependências compatíveis; consulte o procedimento .md."
}

check_only() { cd_project; check_requirements; set_settings_module; run_checks; }

restore() {
  [[ -n "$BACKUP_DIR" ]] || fatal "Informe o diretório do backup."
  cd_project
  [[ -f "$BACKUP_DIR/git_commit.txt" ]] || fatal "Backup inválido: git_commit.txt não encontrado."
  git status --porcelain && fatal "Existem alterações não commitadas; preserve-as antes do restore."
  git reset --hard "$(cat "$BACKUP_DIR/git_commit.txt")"
  if [[ -f "$BACKUP_DIR/database/db.sqlite3" ]]; then
    set_settings_module
    dbfile="$(python_run - <<'PY'
import django
django.setup()
from django.conf import settings
print(settings.DATABASES['default']['NAME'])
PY
)"
    cp -a "$BACKUP_DIR/database/db.sqlite3" "$dbfile"
  else
    warn "O código foi restaurado. Restaure o banco pelo dump em $BACKUP_DIR/database/ usando a ferramenta do seu SGBD."
  fi
  log "Código restaurado para $(cat "$BACKUP_DIR/git_commit.txt"). Reinicie a aplicação e valide os testes."
}

case "$MODE" in
  migrate) migrate ;;
  check) check_only ;;
  restore) restore ;;
  *) sed -n '1,18p' "$0"; exit 2 ;;
esac
