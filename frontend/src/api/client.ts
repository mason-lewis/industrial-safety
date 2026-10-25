export async function request<T>(path:string, body?:unknown, method=body === undefined?'GET':'POST'):Promise<T> {
  const response=await fetch(`/api${path}`,{method,headers:{'Content-Type':'application/json'},...(body===undefined?{}:{body:JSON.stringify(body)})});
  if(!response.ok){
    const text=await response.text();let message=text;
    try{const payload=JSON.parse(text);if(typeof payload.detail==='string')message=payload.detail;else if(Array.isArray(payload.detail))message=payload.detail.map((e:{msg?:string})=>e.msg??'Invalid setting').join('; ');}catch{/* Plain-text server errors are already readable. */}
    throw new Error(message||`${response.status} ${response.statusText}`);
  }
  return response.json();
}
