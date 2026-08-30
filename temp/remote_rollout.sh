#!/usr/bin/env bash
set -Eeuo pipefail

ARCHIVE=${1:?archive path required}
APP=/home/ubuntu/grok-register-panel
UNIT=grok-register-panel.service
UNIT_FILE=/etc/systemd/system/grok-register-panel.service
STAMP=$(date -u +%Y%m%d-%H%M%S)
BACKUP="$APP/backups/rollout-$STAMP"
STAGE="$APP/.rollout-stage-$STAMP"
DEPLOYED=0
ROLLED_BACK=0
FILES=(
  browser_session.py
  grok_register_ttk.py
  run_batch_headless.py
  deploy/grok-register-panel.service.example
)

rollback() {
  local rc=$?
  if [[ "$DEPLOYED" == 1 && "$ROLLED_BACK" == 0 ]]; then
    ROLLED_BACK=1
    printf '%s\n' "rollout failed; restoring previous files" >&2
    for rel in "${FILES[@]}"; do
      if [[ -f "$BACKUP/$rel" ]]; then
        install -o ubuntu -g ubuntu -m 0644 "$BACKUP/$rel" "$APP/$rel"
      fi
    done
    if [[ -f "$BACKUP/grok-register-panel.service" ]]; then
      sudo install -o root -g root -m 0644 "$BACKUP/grok-register-panel.service" "$UNIT_FILE"
    fi
    for marker in .deploy-revision .deploy-time; do
      if [[ -f "$BACKUP/$marker" ]]; then
        install -o ubuntu -g ubuntu -m 0644 "$BACKUP/$marker" "$APP/$marker"
      else
        rm -f "$APP/$marker"
      fi
    done
    sudo systemctl daemon-reload
    sudo systemctl start "$UNIT" || true
    rm -rf "$STAGE"
  fi
  exit "$rc"
}
trap rollback ERR

[[ -f "$ARCHIVE" ]]
mkdir -p "$BACKUP" "$STAGE"
chmod 700 "$BACKUP" "$STAGE"

"$APP/.venv/bin/python" -c 'import sys,tarfile; a=tarfile.open(sys.argv[1]); bad=[m.name for m in a.getmembers() if b"\r\n" in a.extractfile(m).read()]; print("archive_crlf=" + (",".join(bad) if bad else "none")); raise SystemExit(1 if bad else 0)' "$ARCHIVE"
tar -xzf "$ARCHIVE" -C "$STAGE"
"$APP/.venv/bin/python" -c 'import pathlib; root=pathlib.Path(__import__("sys").argv[1]); [p.write_bytes(p.read_bytes().replace(b"\r\n",b"\n").replace(b"\r",b"\n")) for p in root.rglob("*") if p.is_file() and p.suffix in {".py",".sh",".example"}]' "$STAGE"
"$APP/.venv/bin/python" -m py_compile "$STAGE/browser_session.py" "$STAGE/grok_register_ttk.py" "$STAGE/run_batch_headless.py"
PYTHONPATH="$STAGE:$APP" "$APP/.venv/bin/python" -c 'import browser_session,run_batch_headless; print("stage_import_smoke=ok")'

for rel in "${FILES[@]}"; do
  [[ -f "$STAGE/$rel" ]]
  [[ -f "$APP/$rel" ]]
  mkdir -p "$BACKUP/$(dirname "$rel")"
  cp -a "$APP/$rel" "$BACKUP/$rel"
done
sudo cp -a "$UNIT_FILE" "$BACKUP/grok-register-panel.service"
if [[ -f "$APP/config.json" ]]; then
  sudo cp -a "$APP/config.json" "$BACKUP/config.json"
  sudo chmod 600 "$BACKUP/config.json"
fi
if [[ -f /etc/grok-register-panel.env ]]; then
  sudo cp -a /etc/grok-register-panel.env "$BACKUP/grok-register-panel.env"
  sudo chmod 600 "$BACKUP/grok-register-panel.env"
fi
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
if [[ "$(systemctl is-active "$UNIT" 2>/dev/null || true)" == active ]]; then
  printf '%s\n' 'service did not stop within 15 seconds' >&2
  exit 3
fi

for rel in "${FILES[@]}"; do
  install -o ubuntu -g ubuntu -m 0644 "$STAGE/$rel" "$APP/$rel"
done
DEPLOYED=1
sudo python3 -c 'from pathlib import Path; p=Path("/etc/systemd/system/grok-register-panel.service"); s=p.read_text(encoding="utf-8"); old="KillMode=process"; new="KillMode=control-group"; count=s.count(old); count += s.count(new); assert count == 1, f"unexpected KillMode entries: {count}"; p.write_text(s.replace(old,new), encoding="utf-8", newline="\n")'
sudo chmod 644 "$UNIT_FILE"
sudo systemctl daemon-reload

"$APP/.venv/bin/python" -m py_compile "$APP/browser_session.py" "$APP/grok_register_ttk.py" "$APP/run_batch_headless.py"
PYTHONPATH="$APP" "$APP/.venv/bin/python" -c 'import browser_session,run_batch_headless; print("import_smoke=ok")'

ARCHIVE_SHA=$(sha256sum "$ARCHIVE" | { read -r value _; printf '%s' "$value"; })
printf 'rollout-%s-%s\n' "$STAMP" "$ARCHIVE_SHA" > "$APP/.deploy-revision"
printf '%s\n' "$STAMP" > "$APP/.deploy-time"
chmod 644 "$APP/.deploy-revision" "$APP/.deploy-time"

DEPLOYED=1
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
[[ "$(ss -lnt | awk '$4 ~ /:8787$/ {print $4}')" == *:8787 ]]
[[ "$root_code" == 200 ]]
[[ "$health_code" == 200 ]]
grep -q 'def _terminate_profile_process_tree' "$APP/browser_session.py"
grep -q '^KillMode=control-group$' "$UNIT_FILE"
printf 'rollout=%s\n' "$BACKUP"
printf 'archive_sha256=%s\n' "$ARCHIVE_SHA"
printf 'service=%s\n' "$(systemctl is-active "$UNIT")"
printf 'kill_mode=%s\n' "$(systemctl show "$UNIT" -p KillMode --value)"
printf 'root_http=%s\n' "$root_code"
printf 'health_http=%s\n' "$health_code"
printf 'main_pid=%s\n' "$(systemctl show "$UNIT" -p MainPID --value)"
printf 'listener=%s\n' "$(ss -lnt | awk '$4 ~ /:8787$/ {print $4; exit}')"
rm -rf "$STAGE"
trap - ERR
