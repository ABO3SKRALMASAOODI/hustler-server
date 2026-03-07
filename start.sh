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

exec gunicorn --timeout 600 --chdir backend "app:create_app()"
