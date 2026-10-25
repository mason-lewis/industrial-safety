import {describe,it,expect} from 'vitest';
import {renderToStaticMarkup} from 'react-dom/server';
import {batchInputs,folderSorted,intakeFolder,selectAvailable} from './intakeLogic';
import IntakeBrowser from './IntakeBrowser';
import type {LearningIntakeClip} from './generated';
const clip=(id:string,path:string,role:'train'|'valid'|'test'='train',imported=false):LearningIntakeClip=>({id,name:path.split('/').at(-1)!,relative_path:path,bytes:10,source_split:role,collection_id:'neutral',suggested_group:`collection-neutral-${role}`,suggested_split:role,imported_in:imported?[{project_id:'p',clip_id:'c',split:role,frames:4,reviewed:3}]:[]});
describe('folder intake and batch selection',()=>{
 it('sorts source presentation by folder and neutral ID without mutating source records',()=>{
  const rows=[clip('a','folder10/z.mp4'),clip('c','folder2/a.mp4'),clip('b','folder2/z.mp4')];
  expect(folderSorted(rows).map(c=>c.id)).toEqual(['b','c','a']);
  expect(rows.map(c=>c.id)).toEqual(['a','c','b']);expect(intakeFolder(clip('root','x.mp4'))).toBe('Intake root');
 });
 it('adds only available files and preserves selections on other pages/folders',()=>{
  const rows=[clip('new','folder/a.mp4'),clip('old','folder/b.mp4','train',true)];
  expect(selectAvailable(['other','new'],rows)).toEqual(['other','new']);
  expect(selectAvailable(['other'],rows)).toEqual(['other','new']);
 });
 it('uses neutral batch order and excludes UI folder metadata from import assignments',()=>{
  const rows=[clip('z','AAA unsafe/person.mp4'),clip('a','ZZZ safe/forklift.mp4')];
  const inputs=batchInputs(rows,['z','a'],'valid');
  expect(inputs.map(i=>i.intake_id)).toEqual(['a','z']);
  expect(inputs.every(i=>i.split==='valid'&&i.group===undefined)).toBe(true);
  expect(JSON.stringify(inputs)).not.toMatch(/AAA|ZZZ|unsafe|forklift/);
 });
 it('preserves held-out roles and skips imported clips even in a mixed selection',()=>{
  const rows=[clip('a','test/a.mp4','test'),clip('b','valid/b.mp4','valid'),clip('c','train/c.mp4','train',true)];
  expect(batchInputs(rows,['a','b','c'],'train').map(i=>i.split)).toEqual(['test','train']);
  expect(batchInputs(rows,['a','b'],'auto').map(i=>i.split)).toEqual(['test','valid']);
 });
 it('renders folder and status filters with explicit page-wide selection controls',()=>{
  const html=renderToStaticMarkup(<IntakeBrowser projectId="p" busy={false} onImport={async()=>[]}/>);
  for(const text of ['All folders','Select this page','Select all matching files','Clear selection','Already imported','My selected files'])expect(html).toContain(text);
 });
});
