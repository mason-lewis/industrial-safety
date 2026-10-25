export function clock(ms:number|null|undefined):string{
 const seconds=Math.floor(Math.max(0,Number.isFinite(ms)?ms??0:0)/1000);
 const h=Math.floor(seconds/3600),m=Math.floor(seconds%3600/60),s=seconds%60;
 return h?`${h}:${String(m).padStart(2,'0')}:${String(s).padStart(2,'0')}`:`${m}:${String(s).padStart(2,'0')}`;
}
export function seekTarget(ms:number,duration:number|undefined|null):number{
 const target=Number.isFinite(ms)?Math.max(0,ms):0;
 return duration!=null&&duration>0?Math.min(target,duration):target;
}
export interface PlaybackCommand{action:'play'|'pause'|'seek'|'restart'|'step'|'speed';timestamp_ms?:number;speed?:number;resume?:boolean}
