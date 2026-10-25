"""The shipped video workspace, including review-bound Ask."""
from fastapi import FastAPI, HTTPException
from fastapi.staticfiles import StaticFiles
from app.main import websocket
from safety_app.api.main import lifespan, local_boundary, missing, missing_media, invalid, frontend, _web
from safety_app.api.auth import router as auth_router
from safety_app.api.routes import review_runs, video_analysis, monitoring, operations, preview, video_chat
from safety_app.domain.models import AiStatus, BudgetStatus, FrameRef, Job
from safety_app.domain.video_analysis import RecordedSource

app=FastAPI(title='Industrial Safety — Video Review',lifespan=lifespan)
app.middleware('http')(local_boundary)
app.exception_handler(KeyError)(missing)
app.exception_handler(FileNotFoundError)(missing_media)
app.exception_handler(ValueError)(invalid)
app.include_router(auth_router)
app.include_router(review_runs.router)
app.include_router(video_chat.router)
app.include_router(preview.router)
from safety_app.api.routes import chat_lab
app.include_router(chat_lab.router)
app.add_api_route('/api/ai',operations.ai,methods=['GET'],response_model=AiStatus)
app.add_api_route('/api/budgets',operations.budgets,methods=['GET'],response_model=BudgetStatus)
app.add_api_route('/api/budgets/stop',operations.stop,methods=['POST'])
app.add_api_route('/api/video-analysis/sources',video_analysis.recordings,methods=['GET'],response_model=list[RecordedSource])
app.add_api_route('/api/video-analysis',video_analysis.submit,methods=['POST'],response_model=Job)
app.add_api_route('/api/monitoring/uploads',monitoring.upload,methods=['POST'])
app.add_api_route('/api/media/{id}/frames',monitoring.frames,methods=['GET'],response_model=list[FrameRef])
app.add_api_route('/api/media/frames/{id}',monitoring.frame,methods=['GET'])
app.add_api_websocket_route('/api/ws',websocket)
if (_web/'assets').is_dir():app.mount('/assets',StaticFiles(directory=_web/'assets'),name='web-assets')
app.add_api_route('/',frontend,methods=['GET'],include_in_schema=False)

@app.get('/api/health')
def health():return {'status':'ok','workspace':'video_review','computer_vision':'pretrained_person_only'}

@app.get('/test/chat',include_in_schema=False)
def chat_testing_page():
    from safety_app.modules.safety_review.lab_service import enabled
    if not enabled():raise HTTPException(404,'Chat testing is disabled')
    return frontend()

_original_openapi=app.openapi
def testing_openapi():
    import copy
    schema=copy.deepcopy(_original_openapi())
    if not chat_lab.enabled():schema['paths']={key:value for key,value in schema['paths'].items() if not key.startswith('/api/chat-lab')}
    return schema
app.openapi=testing_openapi
