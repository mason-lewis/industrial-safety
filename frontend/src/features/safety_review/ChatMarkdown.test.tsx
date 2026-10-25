import {describe,it,expect} from 'vitest';
import {renderToStaticMarkup} from 'react-dom/server';
import ChatMarkdown from './ChatMarkdown';
const render=(text:string,sourceLinks:Record<string,string>={})=>renderToStaticMarkup(<ChatMarkdown text={text} sourceLinks={sourceLinks}/>);
describe('safe chat Markdown',()=>{
 it('renders headings, emphasis, paragraphs, nested lists and code',()=>{
  const html=render('## Check the scene\n\n**Keep evidence** and *ask questions*.\n\n1. First\n   - Nested\n2. Second\n\nUse `1910.178`.\n\n```js\nconst x = 1;\n```');
  for(const tag of ['<h2>','<strong>','<em>','<ol>','<ul>','<pre>','<code'])expect(html).toContain(tag);
  expect(html).toContain('const x = 1;');
 });
 it('renders GFM tables, task lists and strikethrough',()=>{
  const html=render('| Fact | Status |\n| --- | --- |\n| Date | Unknown |\n\n- [x] Retrieved\n\n~~Unsupported~~');
  expect(html).toContain('chat-markdown-table');expect(html).toContain('<table>');expect(html).toContain('<th>Fact</th>');expect(html).toContain('type="checkbox"');expect(html).toContain('<del>Unsupported</del>');
 });
 it('links known bare citations without changing code, unknown IDs or existing links',()=>{
  const html=render('[source-1] [unknown] [constructor] `[source-1]` [OSHA](https://www.osha.gov)',{'source-1':'#source-turn-source-1'});
  expect(html).toContain('href="#source-turn-source-1"');expect(html).toContain('<code>[source-1]</code>');expect(html).toContain('[unknown]');expect(html).toContain('[constructor]');expect(html).toContain('rel="noopener noreferrer"');
 });
 it('does not execute raw HTML, unsafe URLs or remotely load images',()=>{
  const html=render('<script>alert(1)</script>\n\n[bad](javascript:alert) [local](/api/jobs) ![remote](https://example.com/tracker.png)');
  expect(html).not.toContain('<script');expect(html).not.toContain('javascript:');expect(html).not.toContain('href="/api/jobs"');expect(html).not.toContain('<img');expect(html).toContain('[Image: remote]');
 });
 it('preserves explicit line breaks and literal escape sequences in code',()=>{
  const html=render('First  \nSecond\n\n`\\n`');expect(html).toContain('<br/>');expect(html).toContain('<code>\\n</code>');
 });
});
