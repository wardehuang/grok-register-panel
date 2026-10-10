#!/usr/bin/env bash
set -Eeuo pipefail

ARCHIVE=${1:?archive path required}
APP=/home/ubuntu/grok-register-panel
UNIT=grok-register-panel.service
STAMP=$(date -u +%Y%m%d-%H%M%S)
BACKUP="$APP/backups/rollout-$STAMP"
STAGE="$APP/.rollout-stage-$STAMP"
DEPLOYED=0
ROLLED_BACK=0
REL=register_flow.py

rollback() {
  local rc=$?
  if [[ "$DEPLOYED" == 1 && "$ROLLED_BACK" == 0 ]]; then
    ROLLED_BACK=1
    printf '%s\n' "rollout failed; restoring previous register_flow.py" >&2
    if [[ -f "$BACKUP/$REL" ]]; then
      install -o ubuntu -g ubuntu -m 0644 "$BACKUP/$REL" "$APP/$REL"
    fi
    for marker in .deploy-revision .deploy-time; do
      if [[ -f "$BACKUP/$marker" ]]; then
        install -o ubuntu -g ubuntu -m 0644 "$BACKUP/$marker" "$APP/$marker"
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

"$APP/.venv/bin/python" -c 'import sys,tarfile
a=tarfile.open(sys.argv[1])
crlf=bytes((13,10))
bad=[]
for m in a.getmembers():
    if not m.isfile():
        continue
    data=a.extractfile(m).read()
    if crlf in data:
        bad.append(m.name)
print("archive_crlf=" + (",".join(bad) if bad else "none"))
raise SystemExit(1 if bad else 0)' "$ARCHIVE"

tar -xzf "$ARCHIVE" -C "$STAGE"
"$APP/.venv/bin/python" -c 'import pathlib,sys
root=pathlib.Path(sys.argv[1])
for p in root.rglob("*"):
    if p.is_file() and p.suffix==".py":
        p.write_bytes(p.read_bytes().replace(bytes((13,10)), b"\n").replace(bytes((13,)), b"\n"))
' "$STAGE"
"$APP/.venv/bin/python" -m py_compile "$STAGE/$REL"
grep -q 'def _ensure_signin_cloudflare' "$STAGE/$REL"
grep -q 'SIGNIN_CF_GRACE_SEC = 10.0' "$STAGE/$REL"

[[ -f "$APP/$REL" ]]
cp -a "$APP/$REL" "$BACKUP/$REL"
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
for _ in $(seq 1 20); do
  if [[ "$(systemctl is-active "$UNIT" 2>/dev/null || true)" != active ]]; then
    break
  fi
  sleep 1
done
if [[ "$(systemctl is-active "$UNIT" 2>/dev/null || true)" == active ]]; then
  printf '%s\n' 'service did not stop within 20 seconds' >&2
  exit 3
fi

install -o ubuntu -g ubuntu -m 0644 "$STAGE/$REL" "$APP/$REL"
DEPLOYED=1
"$APP/.venv/bin/python" -m py_compile "$APP/$REL"
grep -q 'def _ensure_signin_cloudflare' "$APP/$REL"

ARCHIVE_SHA=$(sha256sum "$ARCHIVE" | { read -r value _; printf '%s' "$value"; })
printf 'rollout-%s-%s\n' "$STAMP" "$ARCHIVE_SHA" > "$APP/.deploy-revision"
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
listener=$(ss -lnt | awk '$4 ~ /:8787$/ {print $4; exit}')
[[ -n "$listener" ]]
[[ "$root_code" == 200 ]]
[[ "$health_code" == 200 ]]

local_sha=$("$APP/.venv/bin/python" -c 'import hashlib,pathlib,sys; print(hashlib.sha256(pathlib.Path(sys.argv[1]).read_bytes()).hexdigest())' "$APP/$REL")
tar_sha=$("$APP/.venv/bin/python" -c 'import hashlib,tarfile,sys
a=tarfile.open(sys.argv[1])
name=[m.name for m in a.getmembers() if m.isfile()][0]
print(hashlib.sha256(a.extractfile(name).read()).hexdigest())' "$ARCHIVE")
[[ "$local_sha" == "$tar_sha" ]]

printf 'rollout=%s\n' "$BACKUP"
printf 'archive_sha256=%s\n' "$ARCHIVE_SHA"
printf 'file_sha256=%s\n' "$local_sha"
printf 'service=%s\n' "$(systemctl is-active "$UNIT")"
printf 'kill_mode=%s\n' "$(systemctl show "$UNIT" -p KillMode --value)"
printf 'root_http=%s\n' "$root_code"
printf 'health_http=%s\n' "$health_code"
printf 'main_pid=%s\n' "$(systemctl show "$UNIT" -p MainPID --value)"
printf 'listener=%s\n' "$listener"
printf 'revision=%s\n' "$(cat "$APP/.deploy-revision")"
rm -rf "$STAGE"
trap - ERR
