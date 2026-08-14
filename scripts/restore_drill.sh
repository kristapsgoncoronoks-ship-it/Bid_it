#!/usr/bin/env bash
# Restore drill (audit item R14, release gate §3.4).
#
# A backup nobody has read back is not a backup. This script proves the loop
# end to end on a THROWAWAY cluster: build a database at the real migration
# head, put known rows in it, dump it, DESTROY it, restore from the dump alone,
# and check the rows and the schema version came back.
#
# It deliberately destroys its subject. That is the point — a drill that skips
# the destroy step only proves that pg_dump exits zero.
#
# Runs against a scratch cluster on its own port and data directory, so it
# cannot touch a development or production database. Nothing here connects to
# the VPS.
#
#   bash scripts/restore_drill.sh
#
# Exit status is the verdict: 0 = the restore reproduced the data, non-zero =
# the drill FAILED and the backup procedure cannot be trusted.

set -euo pipefail

PGBIN=${PGBIN:-/usr/lib/postgresql/16/bin}
PORT=${PORT:-5455}
# initdb and the server refuse to run as root, so the CLUSTER is owned by an
# unprivileged user. psql and pg_dump are clients and stay in this shell —
# routing them through `su -c` mangles the SQL quoting.
PGUSER_NAME=${PGUSER_NAME:-pguser}
WORK=$(mktemp -d)
chmod 777 "$WORK"
DATADIR="$WORK/data"
DUMP="$WORK/drill.sql.gz"
DB=drilldb
SOCK="$WORK"

cleanup() {
  su "${PGUSER_NAME:-pguser}" -c "$PGBIN/pg_ctl -D $DATADIR -m immediate stop" >/dev/null 2>&1 || true
  rm -rf "$WORK"
}
trap cleanup EXIT

say() { printf '\n=== %s ===\n' "$1"; }

say "1. scratch cluster"
as_pg() { su "$PGUSER_NAME" -c "$*"; }
as_pg "$PGBIN/initdb -D $DATADIR -U drill --auth=trust" >/dev/null
as_pg "$PGBIN/pg_ctl -D $DATADIR -o '-p $PORT -k $SOCK -c listen_addresses=' -w start" >/dev/null
psql() { "$PGBIN/psql" -h "$SOCK" -p "$PORT" -U drill -X -q "$@"; }
psql -d postgres -c "CREATE DATABASE $DB;" >/dev/null
echo "cluster up on port $PORT"

say "2. schema at the real migration head"
export DATABASE_URL="postgresql+asyncpg://drill@/$DB?host=$SOCK&port=$PORT"
(cd backend && .venv/bin/alembic upgrade head >/dev/null)
HEAD_BEFORE=$(cd backend && .venv/bin/alembic current 2>/dev/null | tail -1 | awk '{print $1}')
echo "alembic head: $HEAD_BEFORE"

say "3. known rows"
# Planted through the app's OWN models, not hand-written SQL. Several NOT NULL
# columns take Python-side defaults that a raw INSERT bypasses, and a drill that
# needs its column list updated every time the schema grows is a drill nobody
# runs. This also makes the restored row a real application row.
(cd backend && .venv/bin/python - <<'PYEOF'
import asyncio
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
from app.core.config import settings
from app.models.organization import Organization

async def main():
    engine = create_async_engine(settings.database_url)
    async with async_sessionmaker(engine, expire_on_commit=False)() as s:
        s.add(Organization(id="11111111-1111-1111-1111-111111111111",
                           name="Drill Org", plan="free"))
        await s.commit()
    await engine.dispose()

asyncio.run(main())
PYEOF
) >/dev/null
BEFORE=$(psql -d "$DB" -tAc "SELECT md5(string_agg(id||name||plan, '|' ORDER BY id)) FROM organizations;")
echo "fingerprint before: $BEFORE"

say "4. back up"
"$PGBIN/pg_dump" -h "$SOCK" -p "$PORT" -U drill "$DB" | gzip > "$DUMP"
gzip -t "$DUMP"
echo "dump: $(du -h "$DUMP" | cut -f1), integrity OK"

say "5. DESTROY the database"
psql -d postgres -c "DROP DATABASE $DB;" >/dev/null
if psql -d postgres -tAc "SELECT 1 FROM pg_database WHERE datname='$DB';" | grep -q 1; then
  echo "FAILED: the database survived the drop — the drill proved nothing"; exit 1
fi
echo "gone (verified)"

say "6. restore from the dump alone"
psql -d postgres -c "CREATE DATABASE $DB;" >/dev/null
gunzip -c "$DUMP" | "$PGBIN/psql" -h "$SOCK" -p "$PORT" -U drill -X -q -d "$DB" >/dev/null

say "7. verdict"
AFTER=$(psql -d "$DB" -tAc "SELECT md5(string_agg(id||name||plan, '|' ORDER BY id)) FROM organizations;")
HEAD_AFTER=$(psql -d "$DB" -tAc "SELECT version_num FROM alembic_version;")
TABLES=$(psql -d "$DB" -tAc "SELECT count(*) FROM information_schema.tables WHERE table_schema='public';")

echo "fingerprint after : $AFTER"
echo "alembic after     : $HEAD_AFTER"
echo "tables restored   : $TABLES"

fail=0
[ "$BEFORE" = "$AFTER" ] || { echo "FAILED: row fingerprint differs"; fail=1; }
[ "$HEAD_BEFORE" = "$HEAD_AFTER" ] || { echo "FAILED: schema version differs"; fail=1; }
[ "$TABLES" -gt 50 ] || { echo "FAILED: only $TABLES tables restored"; fail=1; }

if [ "$fail" = 0 ]; then
  echo
  echo "RESTORE DRILL PASSED — data and schema version reproduced from the dump alone."
else
  echo
  echo "RESTORE DRILL FAILED — do not rely on this backup procedure."
fi
exit "$fail"
