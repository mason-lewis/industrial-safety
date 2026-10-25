export type LabelBounds=[number,number,number,number];
export function gestureBounds(kind:'draw'|'move'|'resize',start:[number,number],end:[number,number],original?:LabelBounds):LabelBounds{
 if(kind==='draw')return [Math.min(start[0],end[0]),Math.min(start[1],end[1]),Math.abs(end[0]-start[0]),Math.abs(end[1]-start[1])];
 const [x,y,w,h]=original!;
 if(kind==='move')return [Math.max(0,Math.min(1-w,x+end[0]-start[0])),Math.max(0,Math.min(1-h,y+end[1]-start[1])),w,h];
 return [x,y,Math.min(1-x,Math.max(.003,w+end[0]-start[0])),Math.min(1-y,Math.max(.003,h+end[1]-start[1]))];
}
