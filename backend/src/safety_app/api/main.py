"""FastAPI composition. Existing routes and data remain available during migration."""
from fastapi import Request
from fastapi.responses import JSONResponse,HTMLResponse
from app.main import app as legacy_app, lifespan as legacy_lifespan
from fastapi import FastAPI
from contextlib import asynccontextmanager
@asynccontextmanager
async def lifespan(app):
    from app.main import pipeline
    from safety_app.infrastructure.migration import migrate
    import asyncio
    async with legacy_lifespan(app):
        await asyncio.to_thread(migrate,runtime(),pipeline.repository,pipeline.evidence)
        yield
app=FastAPI(title="Industrial Safety",lifespan=lifespan)
app.include_router(legacy_app.router)
from safety_app.api.routes import monitoring,reviews,datasets,operations,chat,video_analysis,review_runs,preview
from safety_app.api.auth import router,origin,operator
from safety_app.infrastructure.runtime import runtime
for module in (monitoring,reviews,datasets,operations,chat,video_analysis,review_runs,preview):app.include_router(module.router)
app.include_router(router)
# A built frontend lets the course app run on one loopback port.
from pathlib import Path
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
_web=Path(__file__).resolve().parents[4]/'frontend/dist'
if (_web/'assets').is_dir():app.mount('/assets',StaticFiles(directory=_web/'assets'),name='web-assets')
@app.get('/',include_in_schema=False)
def frontend():
    if not (_web/'index.html').is_file():
        return HTMLResponse('Build the frontend with npm --prefix frontend run build, then restart the server.',status_code=503)
    return FileResponse(_web/'index.html')
@app.exception_handler(KeyError)
async def missing(request:Request,error):return JSONResponse(status_code=404,content={'detail':'Record not found'})
@app.exception_handler(FileNotFoundError)
async def missing_media(request:Request,error):return JSONResponse(status_code=404,content={'detail':'Evidence file is missing'})
@app.exception_handler(ValueError)
async def invalid(request:Request,error):return JSONResponse(status_code=400,content={'detail':str(error)[:1000]})
@app.middleware('http')
async def local_boundary(request,call_next):
    from fastapi import HTTPException
    try:
        origin(request)
        if request.method in ('POST','PUT','DELETE','PATCH') and request.url.path!='/api/auth/session':request.state.operator=operator(request)
    except HTTPException as exc:return JSONResponse(status_code=exc.status_code,content={'detail':exc.detail})
    # Reload editable budgets between requests. Workers reload before claiming a job.
    import json
    rt=runtime();rt.config.update(json.loads((rt.root/'configs/agents.yaml').read_text()))
    from app.agents.model_policy import validate_agent_models
    try:validate_agent_models(rt.config)
    except ValueError as exc:return JSONResponse(status_code=503,content={'detail':str(exc)})
    response=await call_next(request)
    return response
