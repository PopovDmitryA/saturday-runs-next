from fastapi import APIRouter

from app.api.routes import (
    achievements,
    admin,
    auth,
    dashboard,
    demo,
    internal_bot,
    internal_vk_bot,
    leaderboards,
    location_ratings,
    locations,
    portal,
    profiles,
    public_profiles,
    runs,
    settings,
    stats,
    sync,
)

api_router = APIRouter()
api_router.include_router(auth.router)
api_router.include_router(stats.router)
api_router.include_router(demo.router)
api_router.include_router(admin.router)
api_router.include_router(profiles.router)
api_router.include_router(dashboard.router)
api_router.include_router(achievements.router)
api_router.include_router(runs.router)
api_router.include_router(leaderboards.router)
api_router.include_router(location_ratings.router)
api_router.include_router(locations.router)
api_router.include_router(portal.router)
api_router.include_router(sync.router)
api_router.include_router(settings.router)
api_router.include_router(public_profiles.router)
api_router.include_router(internal_bot.router)
api_router.include_router(internal_vk_bot.router)
