#!/bin/bash

# Copy template outputs to persistent disk if they don't exist
for dir in /opt/render/project/src/outputs_template/*/; do
  job_id=$(basename "$dir")
  target="/opt/render/project/src/outputs/$job_id"
  if [ ! -d "$target" ]; then
    echo "Restoring template: $job_id"
    cp -r "$dir" "$target"
  fi
done

# ── Clean up orphaned "running" jobs from previous deploys ──────────────
# When Render redeploys, any AA.py subprocess is killed mid-build.
# This leaves state.json stuck on "running" forever. Fix them on boot.
echo "Checking for orphaned running jobs..."
python3 -c "
import os, json, time, psycopg2

OUTPUTS = '/opt/render/project/src/outputs'
fixed = 0

# Fix state.json files on disk
for job_id in os.listdir(OUTPUTS):
    state_path = os.path.join(OUTPUTS, job_id, 'state.json')
    if not os.path.isfile(state_path):
        continue
    try:
        with open(state_path) as f:
            data = json.load(f)
        if data.get('state') == 'running':
            # Check if dist/ exists — if so, build finished but state wasn't updated
            dist_dir = os.path.join(OUTPUTS, job_id, 'dist')
            if os.path.isdir(dist_dir):
                data['state'] = 'completed'
                data['build_ok'] = True
                data['code_changed'] = True
                data['recovered'] = True
            else:
                data['state'] = 'failed'
                data['error'] = 'Build interrupted by server restart'
                data['recovered'] = True
            data['updated_at'] = time.time()
            with open(state_path, 'w') as f:
                json.dump(data, f)
            fixed += 1
            print(f'  Fixed orphaned job: {job_id} -> {data[\"state\"]}')
    except Exception as e:
        print(f'  Error fixing {job_id}: {e}')

# Also fix the DB rows so the frontend sees the correct state
if fixed > 0:
    db_url = os.environ.get('DATABASE_URL')
    if db_url:
        try:
            conn = psycopg2.connect(db_url)
            cur = conn.cursor()
            cur.execute(\"\"\"
                UPDATE jobs SET state = 'failed', updated_at = NOW()
                WHERE state = 'running'
                AND updated_at < NOW() - INTERVAL '2 minutes'
            \"\"\")
            db_fixed = cur.rowcount
            conn.commit()
            cur.close()
            conn.close()
            if db_fixed > 0:
                print(f'  Fixed {db_fixed} stale running jobs in DB')
        except Exception as e:
            print(f'  DB cleanup error: {e}')

if fixed == 0:
    print('  No orphaned jobs found')
else:
    print(f'  Total fixed: {fixed}')
"

exec gunicorn --timeout 600 --chdir backend "app:create_app()"