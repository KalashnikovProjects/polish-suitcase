import assert from 'node:assert/strict';
import { test } from 'node:test';
import { formatDate, formatDateTime, dateInput, parseDateInput, userText } from '../src/lib/presentation.ts';

test('dates keep their calendar day and are written in Russian', () => {
  assert.equal(formatDate('2026-10-27'), '27 октября 2026');
  assert.equal(formatDateTime('2026-10-27T23:30:00-07:00'), '27 октября 2026, 23:30');
  assert.equal(dateInput('2026-10-27'), '27.10.2026');
  assert.equal(parseDateInput('27.10.2026'), '2026-10-27');
});
test('invalid dates cannot be saved as a different calendar day', () => {
  assert.equal(parseDateInput('31.02.2026'), null);
  assert.equal(parseDateInput('29.02.2028'), '2028-02-29');
  assert.equal(parseDateInput('2026-13-10'), null);
  assert.equal(parseDateInput(''), '-');
});
test('historical provider diagnostics are not shown to travellers', () => {
  const copy=userText('Покрытие источника: unsupported checked_baggage requirement is not fully confirmed by synthetic transport source 2026-10-27');
  assert.doesNotMatch(copy,/unsupported|checked_baggage|synthetic|2026-10-27/);
  assert.match(copy,/багажа/);
  assert.match(copy,/27 октября 2026/);
});
