from __future__ import annotations
import traceback
from pathlib import Path as _Path
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from app.api.routers import (advertising,approvals,auth,catalog,dashboard,inventory,orders,products,purchasing,seller_orders,)
from app.config import settings
from app.db.session import init_db
from app.logging_config import get_logger
log=get_logger("amhub.api")
app=FastAPI(title="A&M Commerce Hub",version="0.4.0")
for r in (auth.router,products.router,inventory.router,catalog.router,orders.router,purchasing.router,advertising.router,approvals.router,seller_orders.router,dashboard.router):app.include_router(r)
_WEB_DIR=_Path(__file__).resolve().parent.parent/"web"
if _WEB_DIR.exists():app.mount("/app",StaticFiles(directory=str(_WEB_DIR),html=True),name="web")
@app.get("/",include_in_schema=False)
def _root():return RedirectResponse(url="/app/")
@app.on_event("startup")
def _startup()->None:
 if settings.auto_create_tables:init_db();log.info("startup db_ready=create_all mode=%s",settings.integration_mode)
 else:log.info("startup db_ready=alembic mode=%s",settings.integration_mode)
@app.exception_handler(Exception)
async def _unhandled(request:Request,exc:Exception)->JSONResponse:log.error("unhandled error path=%s %s\n%s",request.url.path,exc,"".join(traceback.format_exception(type(exc),exc,exc.__traceback__)));return JSONResponse(status_code=500,content={"detail":"Internal Server Error"})
@app.get("/health",tags=["system"])
def health()->dict:return {"status":"ok","integration_mode":settings.integration_mode,"version":app.version}