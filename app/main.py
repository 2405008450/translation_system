from fastapi import FastAPI

from app.config import get_settings
from app.routers.web import router as web_router


settings = get_settings()

app = FastAPI(title=settings.app_name)
app.include_router(web_router)
