import {describe,it,expect} from 'vitest';
import {money,reviewStatus,budgetFields} from './budgetInfo';
import type {SceneReview} from '../types/contracts';
describe('budget explanations',()=>{
 it('formats display money to exactly four decimals without changing accounting',()=>{
  expect(money(.1)).toBe('$0.1000');expect(money(19.853116)).toBe('$19.8531');expect(money(.000012)).toBe('$0.0000');
 });
 it('distinguishes legacy pricing errors and new image blocks from spent budgets',()=>{
  expect(reviewStatus({status:'budget_exhausted',error:'Camera budget needs configured request bound or bounded pricing'} as SceneReview)).toBe('Missing price allowance');
  expect(reviewStatus({status:'blocked_media'} as SceneReview)).toBe('Image / media limit');
  expect(reviewStatus({status:'budget_exhausted',error:'Camera generation budget exhausted'} as SceneReview)).toBe('Spending limit');
 });
 it('explains shared UTC windows and each execution cap',()=>{
  expect(budgetFields.map(f=>f.label)).toEqual(['Daily limit (USD)','Monthly limit (USD)','Per workflow limit (USD)','Per agent run limit (USD)']);
  expect(budgetFields[0].description).toContain('UTC');expect(budgetFields[2].description).toContain('chat turn');
 });
});
