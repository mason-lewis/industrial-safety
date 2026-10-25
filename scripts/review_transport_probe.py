"""Read-only model-access/image-count diagnostics. Never invokes generation.

Use --count 100 before paid testing: any failed six-image count means large uploads
from this network are unreliable. Each run writes a new file and never overwrites records."""
import argparse,asyncio,json,sys,time
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path[:0]=[str(ROOT/'backend'),str(ROOT/'backend/src')]
async def main():
 from safety_app.infrastructure.runtime import runtime
 from safety_app.infrastructure.provider_transport import review_client,RequestTrace,current_trace
 from safety_app.infrastructure.responses_model import UsageResponsesModel
 from pydantic_ai.providers.openai import OpenAIProvider
 from pydantic_ai.models import ModelRequestParameters
 from pydantic_ai.messages import ModelRequest,UserPromptPart
 from pydantic_ai import BinaryContent
 rt=runtime();media='952e6697-5ec3-404c-aafb-24b2c7c41ae7'
 frames=rt.media.frames(media,0,10000,6).frames
 content=['Read-only token-count transport probe. No answer requested.']
 for frame in frames:content.extend([frame.model_dump_json(),BinaryContent(data=rt.media.model_image(frame.id),media_type='image/jpeg')])
 results=[]
 for i in range(args.count):
  trace=RequestTrace();token=current_trace.set(trace)
  try:
   async with review_client(10) as client:
    if i%2==0:
     r=await client.models.retrieve('gpt-6-luna');result={'model':r.id}
    else:
     model=UsageResponsesModel('gpt-6-luna',provider=OpenAIProvider(openai_client=client))
     r=await model.count_tokens([ModelRequest(parts=[UserPromptPart(content)])],{'max_tokens':80},ModelRequestParameters());result={'input_tokens':r.input_tokens,'images':len(frames)}
   result|={'ok':True,'transport':trace.diagnostics()}
  except Exception as exc:result={'ok':False,'transport':trace.diagnostics(exc)}
  finally:current_trace.reset(token)
  result|={'operation':'model_access' if i%2==0 else 'image_token_count','generation_dispatched':False};results.append(result);print(json.dumps(result),flush=True)
  await asyncio.sleep(.2)
 out=ROOT/'data/artifacts/reports'/f"network-soak-{time.strftime('%Y%m%dT%H%M%SZ',time.gmtime())}.json"
 with out.open('x') as f:f.write(json.dumps(results,indent=2))
 failed=[r for r in results if not r['ok']]
 print(json.dumps({'requests':len(results),'failed':len(failed),'unsent_proven':sum(bool(r['transport'].get('unsent_basis')) for r in failed),'output':str(out)}))
 return 1 if len(failed)>args.max_failures else 0
parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--count',type=int,default=6);parser.add_argument('--max-failures',type=int,default=0);args=parser.parse_args()
sys.exit(asyncio.run(main()))
