#!/bin/bash
set -euo pipefail
NS=$(cat /var/run/secrets/kubernetes.io/serviceaccount/namespace)
# R: not "restore-check": CloudNativePG creates a ServiceAccount / Role named after each cluster, which would
# overwrite this Job's own (same name) mid-run
CL=postgres R=restore-target DBS="factory shard_a shard_b"
log() { echo "$(date -u +%H:%M:%S) $*"; }
primary=$(kubectl -n "$NS" get cluster "$CL" -o jsonpath='{.status.currentPrimary}')
psql_on() { local pod=$1 db=$2; shift 2; kubectl -n "$NS" exec "$pod" -c postgres -- psql -X -v ON_ERROR_STOP=1 -U postgres -d "$db" -tAc "$*"; }
# exact row count of every table: "table=rows table=rows ..."
COUNTS="SELECT coalesce(string_agg(format('%s=%s', table_name,
  (xpath('/row/c/text()', query_to_xml(format('SELECT count(*) AS c FROM %I', table_name), false, true, '')))[1]::text),
  ' ' ORDER BY table_name), '') FROM information_schema.tables WHERE table_schema = 'public' AND table_type = 'BASE TABLE'"
cleanup() { kubectl -n "$NS" delete cluster "$R" --ignore-not-found --wait=false >/dev/null; }
trap cleanup EXIT
kubectl -n "$NS" delete cluster "$R" --ignore-not-found --wait=true >/dev/null   # leftover of an aborted run

# 1. live: row counts, then a heartbeat that must survive the restore
declare -A want
for db in $DBS; do want[$db]=$(psql_on "$primary" "$db" "$COUNTS"); done
marker="check-$(date +%s)"
psql_on "$primary" postgres "CREATE TABLE IF NOT EXISTS restore_check(marker text, at timestamptz DEFAULT now())" >/dev/null
psql_on "$primary" postgres "DELETE FROM restore_check WHERE at < now() - interval '30 days'" >/dev/null
psql_on "$primary" postgres "INSERT INTO restore_check(marker) VALUES ('$marker')" >/dev/null
wal=$(psql_on "$primary" postgres "SELECT pg_walfile_name(pg_switch_wal())")   # own statement: own transaction
for _ in $(seq 1 120); do
  [ "$(psql_on "$primary" postgres "SELECT last_archived_wal >= '$wal' FROM pg_stat_archiver")" = t ] && break; sleep 2
done
backup=$(kubectl -n "$NS" get backups -o jsonpath='{range .items[?(@.status.phase=="completed")]}{.metadata.name}{"\n"}{end}' | sort | tail -1)
log "live $NS/$primary: heartbeat $marker, WAL $wal archived; latest base backup $backup"

# 2. restore into a throwaway cluster (no WAL archiving: it must never write into the live cluster's archive)
start=$(date +%s)
kubectl -n "$NS" apply -f - >/dev/null <<YAML
apiVersion: postgresql.cnpg.io/v1
kind: Cluster
metadata: { name: $R }
spec:
  instances: 1
  imageName: $(kubectl -n "$NS" get cluster "$CL" -o jsonpath='{.spec.imageName}')
  storage: { size: $(kubectl -n "$NS" get cluster "$CL" -o jsonpath='{.spec.storage.size}') }
  resources:
    requests: { cpu: 100m, memory: 256Mi }
    limits: { memory: 1Gi }
  bootstrap:
    recovery: { source: origin }
  externalClusters:
    - name: origin
      plugin:
        name: barman-cloud.cloudnative-pg.io
        parameters: { barmanObjectName: backups, serverName: $CL }
YAML
phase=""
for _ in $(seq 1 1080); do   # ≤ 90 min
  phase=$(kubectl -n "$NS" get cluster "$R" -o jsonpath='{.status.phase}')
  [ "$phase" = "Cluster in healthy state" ] && break; sleep 5
done
[ "$phase" = "Cluster in healthy state" ] || { log "✗ restored cluster not healthy after 90 min: $phase"; exit 1; }
log "restored in $(( $(date +%s) - start ))s"

# 3. checks
fail=0
got=$(psql_on "$R-1" postgres "SELECT count(*) FROM restore_check WHERE marker = '$marker'")
if [ "$got" = 1 ]; then log "✓ heartbeat restored: the point-in-time chain reaches the check's start"
else log "✗ heartbeat $marker missing in the restored cluster"; fail=1; fi
for db in $DBS; do
  have=$(psql_on "$R-1" "$db" "$COUNTS")
  if [ -n "${want[$db]}" ] && [ "$have" = "${want[$db]}" ]; then log "✓ $db: $(wc -w <<<"$have" | tr -d ' ') tables, row counts equal to live"
  else log "✗ $db: live [${want[$db]}] restored [$have]"; fail=1; fi
done
[ "$fail" = 0 ] || exit 1

# 4. migration rehearsal: the migrations of the next release (the image dev runs now) on this copy of prod's data
#    (millions of rows, dirty values): a migration that would fail, or lock a big table for long, fails here first.
candidate=$(kubectl -n "${CANDIDATE_NAMESPACE:-api-factory-dev}" get deploy api-factory \
  -o jsonpath='{.spec.template.spec.initContainers[?(@.name=="migrate")].image}' 2>/dev/null || true)
if [ -z "$candidate" ]; then log "– migration rehearsal skipped: no candidate image found"; exit 0; fi
current=$(kubectl -n "$NS" get deploy api-factory -o jsonpath='{.spec.template.spec.initContainers[?(@.name=="migrate")].image}')
log "migration rehearsal: $candidate (prod runs $current)"
kubectl -n "$NS" delete pod migrate-rehearsal --ignore-not-found --wait=true >/dev/null
kubectl -n "$NS" apply -f - >/dev/null <<YAML
apiVersion: v1
kind: Pod
metadata: { name: migrate-rehearsal, labels: { app: migrate-rehearsal } }
spec:
  restartPolicy: Never
  imagePullSecrets: [{ name: ghcr-pull }]
  securityContext: { runAsNonRoot: true, runAsUser: 65532, runAsGroup: 65532, seccompProfile: { type: RuntimeDefault } }
  containers:
    - name: migrate
      image: $candidate
      env:
        - { name: PGHOST, value: $R-rw }
        - { name: PGUSER, value: factory }
        - name: PGPASSWORD
          valueFrom: { secretKeyRef: { name: postgres-app, key: password } }   # restored: the same roles as prod
      resources: { requests: { cpu: 50m, memory: 32Mi }, limits: { memory: 128Mi } }
      securityContext: { allowPrivilegeEscalation: false, readOnlyRootFilesystem: true, capabilities: { drop: [ALL] } }
YAML
for _ in $(seq 1 720); do   # ≤ 60 min
  p=$(kubectl -n "$NS" get pod migrate-rehearsal -o jsonpath='{.status.phase}')
  case $p in Succeeded|Failed) break ;; esac; sleep 5
done
kubectl -n "$NS" logs migrate-rehearsal | sed 's/^/    /'
kubectl -n "$NS" delete pod migrate-rehearsal --wait=false >/dev/null
if [ "$p" = Succeeded ]; then log "✓ migration rehearsal: the next release's migrations apply on a copy of prod"
else log "✗ migration rehearsal failed ($p): do not promote this version to prod"; exit 1; fi
