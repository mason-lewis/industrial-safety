import {describe,it,expect} from 'vitest';
import {matchesClass,resolveClasses,displayClass} from './classes';
describe('explicit industrial groups',()=>{
 const groups={vehicles:['truck','forklift'],industrial_vehicles:['forklift']};
 it('expands group targets without inventing detections',()=>{expect(matchesClass('truck',['group:vehicles'],groups)).toBe(true);expect(matchesClass('pizza',['group:vehicles'],groups)).toBe(false);expect(matchesClass('forklift',['vehicles'],groups)).toBe(false);expect(matchesClass('forklift',['group:missing'],groups)).toBe(false);});
 it('resolves unique raw labels and friendly titles',()=>{expect(resolveClasses(['group:vehicles','forklift'],groups)).toEqual(['truck','forklift']);expect(displayClass('group:industrial_vehicles')).toBe('industrial vehicles');});
});
