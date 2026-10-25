export type Point=[number,number];
export function normalizedPoint(x:number,y:number,rect:{left:number;top:number;width:number;height:number}):Point {
 return [Math.max(0,Math.min(1,(x-rect.left)/rect.width)),Math.max(0,Math.min(1,(y-rect.top)/rect.height))];
}
export function matchesContext(a:{session_id?:string;run_generation?:number},b:{session_id?:string;id?:string;run_generation?:number}):boolean {
 return a.session_id === (b.session_id ?? b.id) && (a.run_generation??0) === (b.run_generation??0);
}
export function fittedVideoRect(width:number,height:number,videoWidth:number,videoHeight:number){
 const scale=Math.min(width/videoWidth,height/videoHeight);
 const w=Math.min(width,videoWidth*scale),h=Math.min(height,videoHeight*scale);
 return {left:(width-w)/2,top:(height-h)/2,width:w,height:h};
}
export function moveVertex(points:Point[],index:number,point:Point):Point[]{
 return points.map((p,i)=>i===index?point:p);
}
