"""Authenticated CV-only preview; never dispatch a review or mutate AI state."""
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse, Response

from safety_app.api.auth import Operator, operator
from safety_app.domain.people import PersonPreview, PersonPreviewRequest
from safety_app.infrastructure.runtime import runtime
from safety_app.modules.monitoring.preview import PreviewBusy, preview_path
from safety_app.workflows.video_analysis import local_work

router = APIRouter(prefix='/api/preview')


@router.get('/{source_id}/video')
def video(source_id: str, user: Operator = Depends(operator)):
    return FileResponse(preview_path(runtime().root, source_id))


@router.get('/{source_id}/thumbnail')
async def thumbnail(source_id: str, user: Operator = Depends(operator)):
    raw = await local_work(runtime().preview.thumbnail, source_id)
    return Response(raw, media_type='image/jpeg', headers={'Cache-Control':'private, no-store'})


@router.post('/people', response_model=PersonPreview)
async def people(body: PersonPreviewRequest, user: Operator = Depends(operator)):
    try:
        return await local_work(runtime().preview.inspect, body)
    except PreviewBusy as exc:
        raise HTTPException(429, str(exc)) from exc
