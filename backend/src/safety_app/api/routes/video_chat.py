"""Authenticated review-scoped conversations; reads never start hosted work."""
import asyncio
from fastapi import APIRouter, Depends, HTTPException, Request, Query
from fastapi.responses import StreamingResponse, JSONResponse, Response
from safety_app.api.auth import operator, Operator
from safety_app.infrastructure.runtime import runtime
from safety_app.domain.video_chat import VideoChatInput, VideoChatState, VideoChatTurn
from safety_app.domain.chat_lab import LabTelemetry
from safety_app.modules.safety_review.video_chat import VideoChat

router=APIRouter(prefix='/api/review-runs/{run_id}/chat')

@router.get('',response_model=VideoChatState)
def conversation(run_id:str,user:Operator=Depends(operator)):
    return VideoChat(runtime()).state(run_id,user.site_id)

@router.post('/turns',response_model=VideoChatTurn)
def submit(run_id:str,body:VideoChatInput,user:Operator=Depends(operator)):
    return VideoChat(runtime()).submit(run_id,body,user)

@router.get('/turns/{id}',response_model=VideoChatTurn)
def get(run_id:str,id:str,user:Operator=Depends(operator)):
    return VideoChat(runtime()).get(run_id,id,user.site_id)

@router.post('/turns/{id}/cancel')
def cancel(run_id:str,id:str,user:Operator=Depends(operator)):
    chat=VideoChat(runtime());turn=chat.get(run_id,id,user.site_id)
    if turn.job.status in ('queued','running'):chat.rt.jobs.cancel(id)
    return chat.get(run_id,id,user.site_id).job

@router.get('/turns/{id}/frames/{frame_id}')
def frame(run_id:str,id:str,frame_id:str,user:Operator=Depends(operator)):
    chat=VideoChat(runtime());turn=chat.get(run_id,id,user.site_id)
    if frame_id not in {e.id for e in turn.evidence}:raise HTTPException(404,'Evidence not found in this question')
    return Response(chat.rt.media.image(frame_id),media_type='image/jpeg',headers={'Cache-Control':'private, no-store'})

@router.get('/turns/{id}/activity',response_model=list[LabTelemetry])
def activity(run_id:str,id:str,user:Operator=Depends(operator)):
    return VideoChat(runtime()).events(run_id,id,user.site_id)

@router.get('/export')
def export(run_id:str,user:Operator=Depends(operator)):
    chat=VideoChat(runtime());state=chat.state(run_id,user.site_id)
    return JSONResponse({'conversation':state.model_dump(mode='json'),
        'events':{t.id:[e.model_dump(mode='json') for e in chat.events(run_id,t.id,user.site_id)] for t in state.turns}},
        headers={'Content-Disposition':f'attachment; filename="review-chat-{run_id}.json"','Cache-Control':'private, no-store'})

@router.get('/turns/{id}/events')
async def events(run_id:str,id:str,request:Request,after:int=Query(0,ge=0),user:Operator=Depends(operator)):
    chat=VideoChat(runtime());chat.get(run_id,id,user.site_id)
    try:cursor=max(after,int(request.headers.get('last-event-id','0')))
    except ValueError:raise HTTPException(400,'Invalid event cursor')
    async def stream():
        nonlocal cursor
        quiet=0
        while not await request.is_disconnected():
            rows=await asyncio.to_thread(chat.events,run_id,id,user.site_id,cursor)
            for event in rows:
                cursor=event.sequence
                yield f'id: {cursor}\nevent: activity\ndata: {event.model_dump_json()}\n\n'
            if chat.rt.jobs.get(id).status not in ('queued','running'):
                for event in await asyncio.to_thread(chat.events,run_id,id,user.site_id,cursor):
                    yield f'id: {event.sequence}\nevent: activity\ndata: {event.model_dump_json()}\n\n'
                yield 'event: settled\ndata: {}\n\n';return
            quiet+=1
            if quiet%40==0:yield ': heartbeat\n\n'
            await asyncio.sleep(.25)
    return StreamingResponse(stream(),media_type='text/event-stream',headers={'Cache-Control':'no-store','X-Accel-Buffering':'no'})
