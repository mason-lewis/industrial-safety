/** Refresh selected evidence without overlapping requests or committing after selection changes. */
export function startDetailRefresh<T>(fetchDetail:()=>Promise<T>,commit:(detail:T)=>void,intervalMs=2000){
 let disposed=false;let inFlight=false;
 const poll=async()=>{if(inFlight)return;inFlight=true;try{const detail=await fetchDetail();if(!disposed)commit(detail);}catch{/* Preserve inspectable data during transient connection loss. */}finally{inFlight=false;}};
 const timer=setInterval(poll,intervalMs);
 return ()=>{disposed=true;clearInterval(timer);};
}
