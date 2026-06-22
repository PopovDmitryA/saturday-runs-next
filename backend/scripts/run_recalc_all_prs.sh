#!/bin/bash
set -euo pipefail
cd /opt/saturday-runs-next
/usr/bin/docker compose -f docker-compose.yml -f docker-compose.prod.yml exec -T api \
  python scripts/recalculate_personal_records.py --platform all \
  >> /tmp/recalc_all_prs.log 2>&1
echo "done $(date -Is)" >> /tmp/recalc_all_prs.log
