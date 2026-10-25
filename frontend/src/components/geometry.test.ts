import {describe,it,expect} from 'vitest';
import {normalizedPoint,matchesContext} from './geometry';
describe('overlay frame geometry',()=>{it.each([[1600,900],[640,480],[900,1600]])('aligns normalized coordinates at %s × %s',(width,height)=>{expect(normalizedPoint(10+width*.25,20+height*.75,{left:10,top:20,width,height})).toEqual([.25,.75]);});it('bounds pointer coordinates',()=>{expect(normalizedPoint(-10,200,{left:0,top:0,width:100,height:100})).toEqual([0,1]);});it('rejects overlays from another generation or session',()=>{expect(matchesContext({session_id:'a',run_generation:1},{session_id:'a',run_generation:2})).toBe(false);expect(matchesContext({session_id:'b',run_generation:1},{session_id:'a',run_generation:1})).toBe(false);expect(matchesContext({session_id:'a',run_generation:2},{session_id:'a',run_generation:2})).toBe(true);});});
import {fittedVideoRect,moveVertex} from './geometry';
describe('rule editing geometry',()=>{
 it('accounts for pillarboxing and letterboxing after fullscreen or resolution changes',()=>{
  expect(fittedVideoRect(1000,1000,1920,1080)).toEqual({left:0,top:218.75,width:1000,height:562.5});
  expect(fittedVideoRect(1000,500,500,1000)).toEqual({left:375,top:0,width:250,height:500});
  const r=fittedVideoRect(1000,1000,1920,1080);expect(normalizedPoint(250,r.top+r.height*.75,r)).toEqual([.25,.75]);
 });
 it('drags a vertex without mutating the saved polygon',()=>{const saved:[number,number][]=[[0,0],[1,0],[1,1]];expect(moveVertex(saved,1,[.8,.2])).toEqual([[0,0],[.8,.2],[1,1]]);expect(saved[1]).toEqual([1,0]);});
});
