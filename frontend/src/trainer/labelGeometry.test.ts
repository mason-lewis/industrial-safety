import {describe,it,expect} from 'vitest';
import {gestureBounds} from './labelGeometry';
describe('labeling pointer geometry',()=>{
 it('draws when dragging backwards',()=>{const [x,y,w,h]=gestureBounds('draw',[.8,.7],[.2,.1]);expect(x).toBe(.2);expect(y).toBe(.1);expect(w).toBeCloseTo(.6);expect(h).toBeCloseTo(.6);});
 it('keeps moved boxes inside the image without changing size',()=>{expect(gestureBounds('move',[.5,.5],[1,0],[.4,.4,.3,.2])).toEqual([.7,0,.3,.2]);});
 it('limits resize at image edges',()=>{const [x,y,w,h]=gestureBounds('resize',[.4,.4],[1,1],[.8,.9,.1,.05]);expect(x+w).toBeCloseTo(1);expect(y+h).toBeCloseTo(1);});
 it('never flips box dimensions when resizing past the origin',()=>{const b=gestureBounds('resize',[.5,.5],[0,0],[.3,.3,.2,.2]);expect(b[2]).toBeGreaterThan(0);expect(b[3]).toBeGreaterThan(0);});
 it('handles a thin box at the edge without extending outside the image',()=>{const b=gestureBounds('resize',[.99,.99],[1,1],[.999,.999,.001,.001]);expect(b[0]+b[2]).toBeCloseTo(1);expect(b[1]+b[3]).toBeCloseTo(1);});
});
