#!/usr/bin/env bash
set -Eeuo pipefail

ARCHIVE=${1:?archive path required}
APP=/home/ubuntu/grok-register-panel
UNIT=grok-register-panel.service
STAMP=$(date -u +%Y%m%d-%H%M%S)
BACKUP="$APP/backups/browser-session-hotfix-$STAMP"
STAGE="$APP/.browser-session-hotfix-$STAMP"
DEPLOYED=0
ROLLED_BACK=0

rollback() {
  local rc=$?
  if [[ "$DEPLOYED" == 1 && "$ROLLED_BACK" == 0 ]]; then
    ROLLED_BACK=1
    printf '%s\n' 'hotfix failed; restoring previous browser_session.py' >&2
    install -o ubuntu -g ubuntu -m 0644 "$BACKUP/browser_session.py" "$APP/browser_session.py"
    for marker in .deploy-revision .deploy-time; do
      if [[ -f "$BACKUP/$marker" ]]; then
        install -o ubuntu -g ubuntu -m 0644 "$BACKUP/$marker" "$APP/$marker"
      else
        rm -f "$APP/$marker"
      fi
    done
    sudo systemctl start "$UNIT" || true
    rm -rf "$STAGE"
  fi
  exit "$rc"
}
trap rollback ERR

[[ -f "$ARCHIVE" ]]
mkdir -p "$BACKUP" "$STAGE"
chmod 700 "$BACKUP" "$STAGE"

"$APP/.venv/bin/python" -c 'import sys,tarfile; a=tarfile.open(sys.argv[1]); bad=[m.name for m in a.getmembers() if 13 in a.extractfile(m).read()]; print("archive_crlf=" + (",".join(bad) if bad else "none")); raise SystemExit(1 if bad else 0)' "$ARCHIVE"
tar -xzf "$ARCHIVE" -C "$STAGE"
"$APP/.venv/bin/python" -c 'from pathlib import Path; import sys; root=Path(sys.argv[1]); [p.write_bytes(p.read_bytes().replace(bytes((13,10)),bytes((10,))).replace(bytes((13,)),bytes((10,)))) for p in root.rglob("*") if p.is_file()]' "$STAGE"
"$APP/.venv/bin/python" -m py_compile "$STAGE/browser_session.py"
PYTHONPATH="$STAGE:$APP" "$APP/.venv/bin/python" -c 'import browser_session; print("stage_import_smoke=ok")'

cp -a "$APP/browser_session.py" "$BACKUP/browser_session.py"
for marker in .deploy-revision .deploy-time; do
  if [[ -f "$APP/$marker" ]]; then
    cp -a "$APP/$marker" "$BACKUP/$marker"
  fi
done

batch_procs=$(ps -eo pid=,args= | awk '$0 ~ /\/(run_batch_headless|run_batch_relogin|run_until_100)\.py( |$)/ {print}')
if [[ -n "$batch_procs" ]]; then
  printf '%s\n' "$batch_procs" >&2
  printf '%s\n' 'active batch process detected; aborting before stop' >&2
  exit 2
fi
[[ "$(systemctl is-active "$UNIT")" == active ]]
sudo systemctl stop "$UNIT"
for _ in $(seq 1 15); do
  if [[ "$(systemctl is-active "$UNIT" 2>/dev/null || true)" != active ]]; then
    break
  fi
  sleep 1
done
[[ "$(systemctl is-active "$UNIT" 2>/dev/null || true)" != active ]]

install -o ubuntu -g ubuntu -m 0644 "$STAGE/browser_session.py" "$APP/browser_session.py"
DEPLOYED=1
"$APP/.venv/bin/python" -m py_compile "$APP/browser_session.py"
PYTHONPATH="$APP" "$APP/.venv/bin/python" -c 'import browser_session; print("live_import_smoke=ok")'

ARCHIVE_SHA=$(sha256sum "$ARCHIVE" | { read -r value _; printf '%s' "$value"; })
printf 'browser-session-hotfix-%s-%s\n' "$STAMP" "$ARCHIVE_SHA" > "$APP/.deploy-revision"
printf '%s\n' "$STAMP" > "$APP/.deploy-time"
chmod 644 "$APP/.deploy-revision" "$APP/.deploy-time"

sudo systemctl start "$UNIT"
root_code=000
health_code=000
for _ in $(seq 1 30); do
  [[ "$(systemctl is-active "$UNIT" 2>/dev/null || true)" == active ]] || break
  root_code=$(curl -sS -o /dev/null -w '%{http_code}' --max-time 5 http://127.0.0.1:8787/ || true)
  health_code=$(curl -sS -o /dev/null -w '%{http_code}' --max-time 5 http://127.0.0.1:8787/api/health || true)
  if [[ "$root_code" == 200 && "$health_code" == 200 ]]; then
    break
  fi
  sleep 1
done
[[ "$(systemctl is-active "$UNIT")" == active ]]
[[ "$(systemctl show "$UNIT" -p KillMode --value)" == control-group ]]
[[ "$root_code" == 200 ]]
[[ "$health_code" == 200 ]]
grep -q 'Playwright Sync API 的 Connection' "$APP/browser_session.py"
printf 'rollout=%s\n' "$BACKUP"
printf 'archive_sha256=%s\n' "$ARCHIVE_SHA"
printf 'service=%s\n' "$(systemctl is-active "$UNIT")"
printf 'kill_mode=%s\n' "$(systemctl show "$UNIT" -p KillMode --value)"
printf 'root_http=%s\n' "$root_code"
printf 'health_http=%s\n' "$health_code"
printf 'main_pid=%s\n' "$(systemctl show "$UNIT" -p MainPID --value)"
rm -rf "$STAGE"
trap - ERR
