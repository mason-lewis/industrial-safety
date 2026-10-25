"""Authenticated testing routes. Reading cases or opening the page starts no job."""
import asyncio
import json
from fastapi import APIRouter,Depends,HTTPException,Request,Query
from fastapi.responses import StreamingResponse,JSONResponse
from safety_app.api.auth import operator,Operator
from safety_app.infrastructure.runtime import runtime
from safety_app.domain.chat_lab import LabCapabilities,LabComparisonInput,LabComparison,LabRatingInput,LabTestCase,LabTelemetry
from safety_app.modules.safety_review.lab_service import ChatLab,enabled,hosted_available,settings,CASES

router=APIRouter(prefix='/api/chat-lab')
def testing():
    if not enabled():raise HTTPException(404,'Chat testing is disabled')

@router.get('/capabilities',response_model=LabCapabilities)
def capabilities(user:Operator=Depends(operator)):
    rt=runtime()
    return LabCapabilities(enabled=enabled(),hosted_available=enabled() and hosted_available(rt),workflow_version=settings(rt)['version'] if enabled() else 'disabled')

@router.get('/cases',response_model=list[LabTestCase],dependencies=[Depends(testing)])
def cases(user:Operator=Depends(operator)):return CASES

@router.get('/comparisons',response_model=list[LabComparison],dependencies=[Depends(testing)])
def comparisons(user:Operator=Depends(operator)):return ChatLab(runtime()).list(user.site_id)

@router.post('/comparisons',response_model=LabComparison,dependencies=[Depends(testing)])
def submit(body:LabComparisonInput,user:Operator=Depends(operator)):return ChatLab(runtime()).submit(body,user)

@router.get('/comparisons/{id}',response_model=LabComparison,dependencies=[Depends(testing)])
def get(id:str,user:Operator=Depends(operator)):return ChatLab(runtime()).get(id,user.site_id)

@router.post('/comparisons/{id}/cancel',dependencies=[Depends(testing)])
def cancel(id:str,user:Operator=Depends(operator)):
    lab=ChatLab(runtime());lab.get(id,user.site_id);return lab.rt.jobs.cancel(id)

@router.post('/comparisons/{id}/ratings',dependencies=[Depends(testing)])
def rate(id:str,body:LabRatingInput,user:Operator=Depends(operator)):return ChatLab(runtime()).rate(id,body,user)

@router.get('/comparisons/{id}/export',dependencies=[Depends(testing)])
def export(id:str,user:Operator=Depends(operator)):
    lab=ChatLab(runtime());comparison=lab.get(id,user.site_id)
    return JSONResponse({'comparison':comparison.model_dump(mode='json'),'events':[e.model_dump() for e in lab.events(id,user.site_id)]},headers={'Content-Disposition':f'attachment; filename="chat-comparison-{id}.json"','Cache-Control':'private, no-store'})

@router.get('/comparisons/{id}/events',dependencies=[Depends(testing)])
async def events(id:str,request:Request,after:int=Query(0,ge=0),user:Operator=Depends(operator)):
    lab=ChatLab(runtime());lab.get(id,user.site_id)
    try:cursor=max(after,int(request.headers.get('last-event-id','0')))
    except ValueError:raise HTTPException(400,'Invalid event cursor')
    async def stream():
        nonlocal cursor
        quiet=0
        while not await request.is_disconnected():
            if not enabled():return
            rows=await asyncio.to_thread(lab.events,id,user.site_id,cursor)
            for event in rows:
                cursor=event.sequence
                yield f'id: {cursor}\nevent: activity\ndata: {event.model_dump_json()}\n\n'
            job=lab.rt.jobs.get(id)
            if job.status not in ('queued','running'):
                # Drain events committed between the first read and the terminal status read.
                tail=await asyncio.to_thread(lab.events,id,user.site_id,cursor)
                for event in tail:
                    cursor=event.sequence
                    yield f'id: {cursor}\nevent: activity\ndata: {event.model_dump_json()}\n\n'
                yield 'event: settled\ndata: {}\n\n';return
            quiet+=1
            if quiet%40==0:yield ': heartbeat\n\n'
            await asyncio.sleep(.25)
    return StreamingResponse(stream(),media_type='text/event-stream',headers={'Cache-Control':'no-store','X-Accel-Buffering':'no'})
