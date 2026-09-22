"""Fix incorrect profile_url for RunPark participants.

RunPark participants were incorrectly assigned 5verst profile URLs
(https://5verst.ru/userstats/<id>/) as default. This script replaces them
with the correct RunPark URL (https://runpark.ru/Account/Karmas/<id>).

Usage:
    docker compose exec api python scripts/fix_runpark_profile_urls.py [--dry-run]
"""
import sys

sys.path.insert(0, "/app")

from app.db.session import SessionLocal
from app.models import Participant, Platform

DRY_RUN = "--dry-run" in sys.argv

with SessionLocal() as db:
    platform = db.query(Platform).filter(Platform.code == "runpark").one_or_none()
    if platform is None:
        print("RunPark platform not found")
        sys.exit(1)

    rows = (
        db.query(Participant)
        .filter(Participant.platform_id == platform.id)
        .all()
    )

    fixed = 0
    for p in rows:
        correct_url = f"https://runpark.ru/Account/Karmas/{p.external_user_id}"
        if p.profile_url != correct_url:
            print(f"  {p.external_user_id}: {p.profile_url!r} → {correct_url!r}")
            if not DRY_RUN:
                p.profile_url = correct_url
            fixed += 1

    if not DRY_RUN:
        db.commit()

    print(f"\n{'Would fix' if DRY_RUN else 'Fixed'} {fixed} / {len(rows)} RunPark participants")
    if DRY_RUN:
        print("Run without --dry-run to apply.")
