import assert from 'node:assert/strict';
import { afterEach, test } from 'node:test';
import { apiRequest, invalidateSession, refreshSession } from '../src/lib/api.ts';

const originalFetch = globalThis.fetch;
afterEach(() => { globalThis.fetch = originalFetch; invalidateSession(); });
const tokenResponse = (value: string) => new Response(JSON.stringify({ access_token: value }), { status: 200 });
function deferred() {
  let resolve!: (value: Response) => void;
  const promise = new Promise<Response>(done => { resolve = done; });
  return { promise, resolve };
}

test('parallel expired requests share one refresh', async () => {
  const reply = deferred();
  let calls = 0;
  globalThis.fetch = async () => { calls++; return reply.promise; };
  const first = refreshSession();
  const second = refreshSession();
  assert.equal(first, second);
  reply.resolve(tokenResponse('renewed'));
  assert.equal(await first, 'renewed');
  assert.equal(await second, 'renewed');
  assert.equal(calls, 1);
});

test('old refresh cannot replace or clear a new session refresh', async () => {
  const oldReply = deferred();
  const newReply = deferred();
  let calls = 0;
  globalThis.fetch = async () => ++calls === 1 ? oldReply.promise : newReply.promise;
  const old = refreshSession();
  invalidateSession();
  const fresh = refreshSession();
  oldReply.resolve(tokenResponse('old-user'));
  assert.equal(await old, null);
  assert.equal(refreshSession(), fresh);
  newReply.resolve(tokenResponse('new-user'));
  assert.equal(await fresh, 'new-user');
  assert.equal(calls, 2);
});

test('request from a closed session cannot retry or change the token', async () => {
  const reply = deferred();
  let calls = 0;
  let updates = 0;
  globalThis.fetch = async () => { calls++; return reply.promise; };
  const pending = apiRequest('/api/v1/chats', 'old-token', {}, () => { updates++; });
  invalidateSession();
  reply.resolve(new Response('', { status: 401 }));
  await assert.rejects(pending, /Сессия изменилась/);
  assert.equal(updates, 0);
  assert.equal(calls, 1);
});
