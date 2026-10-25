"""Tool audit wrapper. PydanticAI validates arguments before this wrapper runs."""
import functools
import inspect
import time
from safety_app.domain.models import ToolFailure,uid

def audited(fn):
    if inspect.iscoroutinefunction(fn):
        @functools.wraps(fn)
        async def async_call(ctx,*args,**kwargs):
            d=ctx.deps;id=uid();start=time.monotonic()
            bound=inspect.signature(fn).bind(ctx,*args,**kwargs);bound.apply_defaults();arguments={k:(v.model_dump(mode='json') if hasattr(v,'model_dump') else v) for k,v in bound.arguments.items() if k!='ctx'}
            d.audit.write('tool_started',status='running',run_id=d.run_id,job_id=d.job_id,agent_name=d.agent_name,tool_call_id=id,tool_name=fn.__name__,arguments=arguments)
            try:
                if d.cancelled():raise ValueError('Run cancelled')
                d.budgets.tool(d.run_id)
                result=await fn(ctx,*args,**kwargs)
                serial=result.model_dump(mode='json') if hasattr(result,'model_dump') else result
                d.audit.write('tool_finished',run_id=d.run_id,job_id=d.job_id,agent_name=d.agent_name,tool_call_id=id,tool_name=fn.__name__,arguments=arguments,output=serial,duration_ms=(time.monotonic()-start)*1000)
                return result
            except BaseException as exc:
                d.audit.write('tool_finished',status='cancelled' if isinstance(exc,__import__('asyncio').CancelledError) else 'failed',run_id=d.run_id,job_id=d.job_id,agent_name=d.agent_name,tool_call_id=id,tool_name=fn.__name__,arguments=arguments,error=ToolFailure(code=type(exc).__name__,message=str(exc)),duration_ms=(time.monotonic()-start)*1000)
                raise
        return async_call
    @functools.wraps(fn)
    def call(ctx,*args,**kwargs):
        d=ctx.deps;id=uid();start=time.monotonic()
        bound=inspect.signature(fn).bind(ctx,*args,**kwargs);bound.apply_defaults();arguments={k:v for k,v in bound.arguments.items() if k!='ctx'}
        d.audit.write('tool_started',status='running',run_id=d.run_id,job_id=d.job_id,agent_name=d.agent_name,tool_call_id=id,tool_name=fn.__name__,arguments=arguments)
        try:
            if d.cancelled():raise ValueError('Run cancelled')
            d.budgets.tool(d.run_id)
            result=fn(ctx,*args,**kwargs)
            serial=result.return_value if hasattr(result,'return_value') else result
            serial=serial.model_dump(mode='json') if hasattr(serial,'model_dump') else serial
            d.audit.write('tool_finished',run_id=d.run_id,job_id=d.job_id,agent_name=d.agent_name,tool_call_id=id,tool_name=fn.__name__,arguments=arguments,output=serial if isinstance(serial,(dict,list)) else {'value':serial},duration_ms=(time.monotonic()-start)*1000)
            return result
        except Exception as exc:
            d.audit.write('tool_finished',status='failed',run_id=d.run_id,job_id=d.job_id,agent_name=d.agent_name,tool_call_id=id,tool_name=fn.__name__,arguments=arguments,error=ToolFailure(code=type(exc).__name__,message=str(exc)),duration_ms=(time.monotonic()-start)*1000)
            raise
    return call
