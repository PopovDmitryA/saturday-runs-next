.PHONY: up down migrate test lint backend-test frontend-build location-mapping sync

up:
	docker compose up --build

down:
	docker compose down

migrate:
	cd backend && alembic upgrade head

test: backend-test frontend-lint

lint:
	cd backend && ruff check app tests && mypy app

backend-test:
	cd backend && pytest

frontend-lint:
	cd frontend && npm run lint

frontend-build:
	docker run --rm -v $(CURDIR)/frontend:/app -w /app node:22-alpine sh -c "npm ci && npm run build"

location-mapping:
	python3 backend/scripts/build_location_mapping_xlsx.py

# Интерактивное меню обновления (prod/local, dry-run, выбор цифрами)
sync:
	bash scripts/sync.sh

location-catalog-import:
	python3 backend/scripts/import_location_catalog.py --import-db

location-catalog-import-docker:
	docker compose cp data/location_catalog.json api:/tmp/location_catalog.json
	docker compose exec -T api python scripts/import_location_catalog.py --json /tmp/location_catalog.json --from-json --import-db

location-csv-import:
	python3 backend/scripts/import_platform_locations_csv.py --import-db

location-csv-import-docker:
	docker compose cp data/locations/s95_locations.csv api:/tmp/s95_locations.csv
	docker compose cp data/locations/five_verst_locations.csv api:/tmp/five_verst_locations.csv
	docker compose exec -T api python scripts/import_platform_locations_csv.py \
		--s95-csv /tmp/s95_locations.csv \
		--five-verst-csv /tmp/five_verst_locations.csv \
		--import-db

runpark-mapping-import:
	cd backend && python scripts/import_runpark_location_mappings.py --geocode --import-db

runpark-mapping-import-docker:
	docker compose cp data/runpark_import/vw_locations_202605271905_matched.xlsx api:/tmp/runpark_locations.xlsx
	docker compose exec -T api python scripts/import_runpark_location_mappings.py \
		--xlsx /tmp/runpark_locations.xlsx \
		--json /tmp/runpark_location_mappings.json \
		--geocode --import-db

# Process profile_fetch_pending queue (ARGS e.g. --clear-cooldown --platform parkrun)
process-pending-fetches:
	docker compose exec -T api python scripts/process_pending_profile_fetches.py $(ARGS)

# Visible browser + save cookies after manual captcha (set env vars in .env first)
parkrun-save-browser-state:
	docker compose exec api python scripts/parkrun_save_browser_state.py

# Mac: one command — DB queue, Chrome, wait for captcha, sync runs (run daily)
parkrun:
	bash scripts/parkrun_daemon.sh

# Seed 5 parkrun profiles from legacy five_verst_stats into LK queue (needs LEGACY_DATABASE_URL)
parkrun-seed-queue:
	bash scripts/seed_parkrun_queue.sh

# Legacy ETL (needs LEGACY_DATABASE_URL + DATABASE_URL; use --dry-run first)
migrate-legacy-dry-run:
	docker compose exec api python scripts/migrate_from_legacy.py --platform five_verst --dry-run --steps locations,events

migrate-legacy-validate:
	docker compose exec api python scripts/migrate_from_legacy.py --validate --platform all --pretty

# Chrome with --remote-debugging-port=9222 on Mac; bypasses host.docker.internal CDP 500
parkrun-save-cdp-host:
	bash scripts/parkrun_save_cdp_host.sh

# Process parkrun pending queue through open Chrome (Mac; LIMIT=5 by default)
process-pending-parkrun-host:
	bash scripts/process_pending_parkrun_host.sh

# Local script runs against prod PostgreSQL (SSH tunnel from .env TEMP_SSH_* / TEMP_PROD_PG_*).
# Needs local Redis: docker compose up -d redis
# Dry-run (no DB writes): make prod-run ARGS="scripts/five_verst_sync_latest.py --dry-run --pretty"
# Write to prod:           CONFIRM_PROD=1 make prod-run ARGS="scripts/five_verst_sync_location.py --slug bitsa --protocols 1"
prod-run:
	bash scripts/run_prod_script.sh $(ARGS)

prod-tunnel:
	bash -c 'source scripts/prod_db_env.sh && start_prod_db_tunnel'

prod-tunnel-stop:
	bash -c 'source scripts/prod_db_env.sh && stop_prod_db_tunnel'

# Long-running worker for admin button (keep terminal open)
parkrun-local-worker:
	bash scripts/parkrun_local_worker.sh

# Sync runs for already-linked parkrun profile (Mac CDP; ATHLETE_ID=3197430)
parkrun-sync-linked-host:
	bash scripts/parkrun_sync_linked_host.sh
