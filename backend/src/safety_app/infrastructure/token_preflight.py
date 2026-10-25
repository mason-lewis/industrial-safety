"""Count the actual PydanticAI Responses payload, including images and schemas."""
import asyncio
from dataclasses import fields, is_dataclass
import hashlib
from pydantic_ai import BinaryContent
from safety_app.infrastructure.exact_cache import ExactCache, fingerprint


class TokenPreflightUnavailable(RuntimeError):
    """Provider availability failure, not an exhausted dollar/token allowance."""


def transient_count_failure(error):
    """Read-only counting can recover; generation retries only proven-unsent requests."""
    seen=set()
    while error is not None and id(error) not in seen:
        seen.add(id(error))
        status=getattr(error,'status_code',None)
        if status is not None:
            return status in {408,429,500,502,503,504}
        if type(error).__name__ in {'APIConnectionError','APITimeoutError','ConnectError',
            'ReadError','WriteError','RemoteProtocolError','ConnectTimeout','ReadTimeout','WriteTimeout','PoolTimeout'}:
            return True
        error=error.__cause__ or error.__context__
    return False


def canonical(value):
    if isinstance(value, BinaryContent):
        return {'image':hashlib.sha256(value.data).hexdigest(),'media_type':value.media_type,
                'vendor_metadata':value.vendor_metadata}
    if isinstance(value, bytes):return {'sha256':hashlib.sha256(value).hexdigest()}
    if isinstance(value, dict):return {str(k):canonical(v) for k,v in value.items()}
    if isinstance(value, (tuple,list)):return [canonical(v) for v in value]
    if is_dataclass(value):return {f.name:canonical(getattr(value,f.name)) for f in fields(value) if f.name!='timestamp'}
    if hasattr(value,'model_dump'):return canonical(value.model_dump(mode='json'))
    if value is None or isinstance(value,(str,int,float,bool)):return value
    return str(value)


async def count_input(model, messages, settings, parameters, ledger, audit, run_id, cancelled, job_id=None, workflow_id=None):
    cache=ExactCache(ledger.db, {'purpose':'input-preflight-1','model':model.model_name,
                               'pydantic_ai':'2.54.0','site':getattr(ledger,'site_id','local')})
    key={'payload':fingerprint(canonical([messages,settings,parameters]))}
    found=cache.get('token_count',key)
    if found is not None:
        audit.write('cache_hit',run_id=run_id,job_id=job_id,parent_run_id=workflow_id,resources={'category':'token_count'})
        return found['input_tokens']
    from safety_app.infrastructure.provider_transport import RequestTrace,current_trace
    task=None
    try:
        async with asyncio.timeout(20):
            for attempt in (1,2,3):
                if cancelled() or ledger.state('stopped')=='1':raise asyncio.CancelledError()
                trace=RequestTrace();trace_token=current_trace.set(trace)
                resources={'payload_fingerprint':key['payload'],'preflight_attempt':attempt,'generation_dispatched':False,
                           'policy_version':'exact-count-transient-retry-3'}
                audit.write('token_preflight_started',status='running',run_id=run_id,job_id=job_id,parent_run_id=workflow_id,resources=resources)
                task=asyncio.create_task(model.count_tokens(messages,settings,parameters))
                try:
                    while not task.done():
                        if cancelled() or ledger.state('stopped')=='1':raise asyncio.CancelledError()
                        await asyncio.wait({task},timeout=.1)
                    usage=await task
                    count=usage.input_tokens
                    if not isinstance(count,int) or count<=0:raise ValueError('Invalid token count')
                    cache.put('token_count',key,{'input_tokens':count},1800)
                    audit.write('token_preflight_completed',run_id=run_id,job_id=job_id,parent_run_id=workflow_id,resources=resources|{'input_tokens':count,'basis':'provider_input_tokens_endpoint','transport':trace.diagnostics()})
                    return count
                except asyncio.CancelledError:raise
                except Exception as exc:
                    retry=attempt<3 and transient_count_failure(exc)
                    audit.write('token_preflight_failed',status='failed',run_id=run_id,job_id=job_id,parent_run_id=workflow_id,resources=resources|{'code':type(exc).__name__,'transport':trace.diagnostics(exc),'read_only_retry_scheduled':retry})
                    if not retry:raise
                finally:
                    if not task.done():task.cancel()
                    await asyncio.gather(task,return_exceptions=True)
                    current_trace.reset(trace_token)
                # Count-only recovery, inside the same deadline, with no reservation.
                await asyncio.sleep(.2*attempt**2)
    except asyncio.CancelledError:raise
    except Exception as exc:
        raise TokenPreflightUnavailable('OpenAI image token preflight is unavailable. Generation was not dispatched and no new reservation was created. Check connectivity or provider availability before another submission.') from exc
    finally:
        if task is not None and not task.done():
            task.cancel()
            await asyncio.gather(task,return_exceptions=True)
