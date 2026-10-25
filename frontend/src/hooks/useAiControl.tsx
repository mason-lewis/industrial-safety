import {createContext,useContext,useEffect,useRef,useState,type ReactNode} from 'react';
import {request} from '../api/client';
import type {AiStatus} from '../api/generated';

type Control={status:AiStatus|null;busy:boolean;error:string;toggle:()=>Promise<void>};
const Context=createContext<Control|null>(null);

export function AiControlProvider({children}:{children:ReactNode}){
 const [status,setStatus]=useState<AiStatus|null>(null),[busy,setBusy]=useState(false),[error,setError]=useState('');
 const revision=useRef(0),locked=useRef(false);
 useEffect(()=>{
  let disposed=false,timer:ReturnType<typeof setTimeout>;
  async function refresh(){
   const version=revision.current;
   try{const next=await request<AiStatus>('/ai');if(!disposed&&version===revision.current){setStatus(next);setError('');}}
   catch{if(!disposed&&version===revision.current){setStatus(null);setError('Could not reach AI controls. Check the local server.');}}
   if(!disposed)timer=setTimeout(refresh,1500);
  }
  void refresh();return()=>{disposed=true;clearTimeout(timer);};
 },[]);
 async function toggle(){
  if(!status||locked.current)return;
  locked.current=true;revision.current++;setBusy(true);setError('');
  try{await request('/budgets/stop',{stopped:status.enabled});setStatus(await request<AiStatus>('/ai'));}
  catch(e){setStatus(null);setError(e instanceof Error?e.message:String(e));}
  finally{locked.current=false;revision.current++;setBusy(false);}
 }
 return <Context.Provider value={{status,busy,error,toggle}}>{children}</Context.Provider>;
}

export function useAiControl(){
 const control=useContext(Context);
 if(!control)throw new Error('AI controls require the authenticated workspace');
 return control;
}
