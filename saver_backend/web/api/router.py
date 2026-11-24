from fastapi.routing import APIRouter

from saver_backend.web.api import instagram, monitoring

api_router = APIRouter()
api_router.include_router(monitoring.router)
api_router.include_router(instagram.router)
