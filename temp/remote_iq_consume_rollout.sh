#!/usr/bin/env bash
set -euo pipefail

ARCHIVE="${1:?archive path required}"
APP=/home/ubuntu/grok-register-panel
UNIT=grok-register-panel.service
STAMP="$(date -u +%Y%m%d-%H%M%S)"
BACKUP="$APP/backups/rollout-$STAMP"
STAGE="$APP/.rollout-stage-$STAMP"
PY="$APP/.venv/bin/python"
FILES=(
  intelligence_check.py
  webui/quality_proxy_store.py
  webui/monitor.py
)

DEPLOYED=0
ROLLED_BACK=0

rollback() {
  if [[ "$DEPLOYED" -ne 1 || "$ROLLED_BACK" -eq 1 ]]; then
    return 0
  fi
  ROLLED_BACK=1
  echo "ROLLBACK start"
  for rel in "${FILES[@]}"; do
    if [[ -f "$BACKUP/$rel" ]]; then
      install -m 0644 "$BACKUP/$rel" "$APP/$rel"
    fi
  done
  if [[ -f "$BACKUP/.deploy-revision" ]]; then
    install -m 0644 "$BACKUP/.deploy-revision" "$APP/.deploy-revision"
  else
    rm -f "$APP/.deploy-revision"
  fi
  if [[ -f "$BACKUP/.deploy-time" ]]; then
    install -m 0644 "$BACKUP/.deploy-time" "$APP/.deploy-time"
  else
    rm -f "$APP/.deploy-time"
  fi
  rm -rf "$STAGE"
  sudo systemctl start "$UNIT" || true
  echo "ROLLBACK done"
}

on_err() {
  echo "ERROR at line $1"
  rollback
}
trap 'on_err $LINENO' ERR

require_no_live_batch() {
  "$PY" - <<'PY'
import sys
needles = (
    "run_batch_headless.py",
    "run_until_100.py",
    "run_batch_relogin.py",
)
live = []
import subprocess
out = subprocess.check_output(["ps", "-eo", "pid=,stat=,args="], text=True)
for line in out.splitlines():
    parts = line.strip().split(None, 2)
    if len(parts) < 3:
        continue
    pid, stat, args = parts
    if stat.startswith("Z"):
        continue
    if "remote_iq_consume_rollout.sh" in args:
        continue
    if any(n in args for n in needles):
        live.append(line.strip())
if live:
    sys.stderr.write("LIVE BATCH, abort\n" + "\n".join(live) + "\n")
    sys.exit(2)
print("no live batch")
PY
}

wait_monitor_gone() {
  local i
  for i in $(seq 1 30); do
    if ! pgrep -f "$APP/.venv/bin/python -u $APP/webui/monitor.py" >/dev/null 2>&1; then
      echo "monitor gone"
      return 0
    fi
    sleep 1
  done
  echo "monitor still running after stop"
  return 1
}

echo "=== preflight ==="
[[ -f "$ARCHIVE" ]]
require_no_live_batch
echo "archive=$ARCHIVE"
ARCHIVE_SHA="$("$PY" - <<PY
from pathlib import Path
import hashlib
p = Path("$ARCHIVE")
print(hashlib.sha256(p.read_bytes()).hexdigest())
PY
)"
echo "archive_sha256=$ARCHIVE_SHA"

echo "=== archive CRLF scan ==="
"$PY" - <<PY
import tarfile, sys
crlf = bytes((13, 10))
with tarfile.open("$ARCHIVE", "r:gz") as tar:
    for member in tar.getmembers():
        if not member.isfile():
            continue
        data = tar.extractfile(member).read()
        if crlf in data:
            sys.stderr.write("CRLF in archive member: %s\n" % member.name)
            sys.exit(3)
        print("lf_ok", member.name, member.size)
print("archive_lf_ok")
PY

echo "=== backup ==="
mkdir -p "$BACKUP/webui"
for rel in "${FILES[@]}"; do
  if [[ -f "$APP/$rel" ]]; then
    install -m 0644 "$APP/$rel" "$BACKUP/$rel"
  fi
done
if [[ -f "$APP/config.json" ]]; then
  install -m 0600 "$APP/config.json" "$BACKUP/config.json"
fi
if [[ -f "$APP/.deploy-revision" ]]; then
  install -m 0644 "$APP/.deploy-revision" "$BACKUP/.deploy-revision"
fi
if [[ -f "$APP/.deploy-time" ]]; then
  install -m 0644 "$APP/.deploy-time" "$BACKUP/.deploy-time"
fi
echo "backup=$BACKUP"

echo "=== stage ==="
rm -rf "$STAGE"
mkdir -p "$STAGE"
tar -C "$STAGE" -xzf "$ARCHIVE"
"$PY" - <<PY
from pathlib import Path
crlf = bytes((13, 10))
root = Path("$STAGE")
for path in list(root.rglob("*.py")) + list(root.rglob("*.sh")):
    data = path.read_bytes().replace(bytes((13, 10)), bytes((10,))).replace(bytes((13,)), bytes((10,)))
    if crlf in data:
        raise SystemExit("stage CRLF " + str(path))
    path.write_bytes(data)
    print("stage_lf", path.relative_to(root))
PY

echo "=== compile stage ==="
"$PY" -m py_compile \
  "$STAGE/intelligence_check.py" \
  "$STAGE/webui/quality_proxy_store.py" \
  "$STAGE/webui/monitor.py"
echo "py_compile_ok"

echo "=== import smoke ==="
"$PY" - <<PY
import ast
import importlib.util
import sys
from pathlib import Path
sys.path.insert(0, "$APP")
stage = Path("$STAGE")
store_path = stage / "webui" / "quality_proxy_store.py"
check_path = stage / "intelligence_check.py"
monitor_path = stage / "webui" / "monitor.py"
spec = importlib.util.spec_from_file_location("quality_proxy_store_stage", store_path)
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)
assert callable(mod.claim_runtime_quality_proxy)
check_src = check_path.read_text(encoding="utf-8")
ast.parse(check_src)
assert "claim_runtime_quality_proxy()" in check_src
assert "智商通过，已删除" in check_src
assert "每号独占一条" in monitor_path.read_text(encoding="utf-8")
print("import_smoke_ok", store_path, check_path)
PY

echo "=== stop service ==="
sudo systemctl stop "$UNIT"
wait_monitor_gone
# stale pid files: only remove if target is dead/zombie
"$PY" - <<'PY'
from pathlib import Path
app = Path("/home/ubuntu/grok-register-panel")
for rel in ("log/batch100.pid", "log/orch100.pid", "log/batch_relogin.pid", "log/recovery.pid"):
    path = app / rel
    if not path.is_file():
        continue
    text = path.read_text(encoding="utf-8").strip()
    if not text.isdigit():
        continue
    proc = Path("/proc") / text
    if not proc.exists():
        path.unlink()
        print("removed_stale_pid", rel, text)
        continue
    stat = (proc / "stat").read_text(encoding="utf-8", errors="replace")
    # zombie state is Z
    fields = stat.split()
    if len(fields) > 2 and fields[2] == "Z":
        path.unlink()
        print("removed_zombie_pid", rel, text)
    else:
        print("keep_live_pid", rel, text)
PY

echo "=== install ==="
for rel in "${FILES[@]}"; do
  [[ -f "$STAGE/$rel" ]]
  [[ -f "$APP/$rel" ]]
  install -m 0644 "$STAGE/$rel" "$APP/$rel"
done
DEPLOYED=1
printf 'rollout-%s-%s\n' "$STAMP" "$ARCHIVE_SHA" > "$APP/.deploy-revision"
printf '%s\n' "$STAMP" > "$APP/.deploy-time"
chmod 0644 "$APP/.deploy-revision" "$APP/.deploy-time"

echo "=== verify installed markers ==="
grep -F "claim_runtime_quality_proxy" "$APP/webui/quality_proxy_store.py" >/dev/null
grep -F "智商通过，已删除" "$APP/intelligence_check.py" >/dev/null
grep -F "每号独占一条" "$APP/webui/monitor.py" >/dev/null
"$PY" - <<PY
import hashlib, tarfile
from pathlib import Path
app = Path("$APP")
with tarfile.open("$ARCHIVE", "r:gz") as tar:
    for member in tar.getmembers():
        if not member.isfile():
            continue
        packed = tar.extractfile(member).read()
        live = (app / member.name).read_bytes()
        if hashlib.sha256(packed).digest() != hashlib.sha256(live).digest():
            raise SystemExit("sha mismatch " + member.name)
        print("sha_match", member.name, hashlib.sha256(live).hexdigest())
print("sha_all_match")
PY

echo "=== start ==="
sudo systemctl start "$UNIT"
ok=0
for i in $(seq 1 30); do
  if [[ "$(systemctl is-active "$UNIT")" == "active" ]]; then
    root_code="$(curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1:8787/ || true)"
    health_code="$(curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1:8787/api/health || true)"
    if [[ "$root_code" == "200" && "$health_code" == "200" ]]; then
      ok=1
      echo "health_ok loop=$i root=$root_code health=$health_code"
      break
    fi
    echo "wait health loop=$i root=$root_code health=$health_code"
  else
    echo "wait active loop=$i state=$(systemctl is-active "$UNIT" || true)"
  fi
  sleep 1
done
if [[ "$ok" -ne 1 ]]; then
  echo "health failed"
  exit 4
fi

echo "=== post ==="
systemctl is-active "$UNIT"
systemctl show "$UNIT" -p MainPID -p KillMode -p ExecMainStartTimestamp --no-page
ss -lntp | grep 8787 || true
curl -s -o /dev/null -w "root=%{http_code}\n" http://127.0.0.1:8787/
curl -s -o /dev/null -w "health=%{http_code}\n" http://127.0.0.1:8787/api/health
curl -s -o /dev/null -w "status=%{http_code}\n" http://127.0.0.1:8787/api/status
curl -s -o /dev/null -w "quality=%{http_code}\n" http://127.0.0.1:8787/api/quality-proxies
echo "revision=$(cat "$APP/.deploy-revision")"
echo "time=$(cat "$APP/.deploy-time")"
rm -rf "$STAGE"
echo "DEPLOY_OK"
