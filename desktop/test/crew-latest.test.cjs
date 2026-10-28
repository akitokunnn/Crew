'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const { checkLatest, compareVersions } = require('../src/crew-latest.cjs');
const API = 'https://api.github.com/repos/akitokunnn/Crew';
const branchRoute = '/commits/rebuild%2Fv2';
const build = {appId:'local.crew.desktop.v2'};
const sha = '0123456789abcdef0123456789abcdef01234567';
const stamp = '2026-10-25T08:34:39Z';
const commit = { sha, commit: { committer: { date: stamp } } };
const contents = value => ({ path: 'desktop/package.json', encoding: 'base64',
  content: Buffer.from(JSON.stringify(value)).toString('base64') + '\n' });
function response(value, status = 200, properties = {}) {
  return { ok: status >= 200 && status < 300, status, redirected: false,
    text: async () => JSON.stringify(value), ...properties };
}
function fixture(overrides = {}) {
  const calls = [];
  const routes = { [branchRoute]: response(commit),
    ['/contents/desktop/package.json?ref=' + sha]: response(contents({ name: 'crew-desktop', version: '0.2.5', build })), ...overrides };
  return { calls, fetchImpl: async (url, options) => {
    calls.push({ url, options });
    const result = routes[url.slice(API.length)];
    if (result instanceof Error) throw result;
    assert.ok(result, 'Unexpected URL: ' + url);
    return result;
  } };
}

test('V2 source version is pinned to the observed branch commit without reading the V1 release feed', async () => {
  const f = fixture();
  const value = await checkLatest({ fetchImpl: f.fetchImpl, now: () => stamp });
  assert.equal(value.release, null); assert.equal(value.main.version, '0.2.5'); assert.equal(value.main.sha, sha);
  assert.equal(value.checked_at, stamp); assert.match(value.main.url, new RegExp('/blob/' + sha + '/desktop/package.json$'));
  assert.equal(f.calls.length, 2); assert.equal(value.branch,'rebuild/v2'); assert.equal(value.main.branch,'rebuild/v2');
  for (const call of f.calls) {
    assert.equal(call.options.redirect, 'error'); assert.equal(call.options.credentials, 'omit');
    assert.equal(call.options.headers.Authorization, undefined); assert.ok(call.options.signal instanceof AbortSignal);
  }
});

test('V1 main and latest release are never used even if they advertise a newer version', async () => {
  const f = fixture({ '/releases/latest': response({ tag_name: 'v99.0.0' }), '/commits/main': response(commit) });
  const value = await checkLatest({ fetchImpl: f.fetchImpl });
  assert.equal(value.release,null); assert.equal(value.main.version, '0.2.5');
  assert.equal(f.calls.some(call=>/releases|commits\/main/.test(call.url)),false);
});

test('network, HTTP failures and rate limits remain visible', async () => {
  for (const [result, pattern] of [[new Error('offline'), /offline/], [response({}, 403), /受限.*403/],
    [response({}, 429), /受限.*429/], [response({}, 500), /HTTP 500/]]) {
    const f = fixture({ [branchRoute]: result });
    await assert.rejects(checkLatest({ fetchImpl: f.fetchImpl }), pattern);
    assert.equal(f.calls.length, 1);
  }
});

test('redirects, malformed JSON, wrong repository package and invalid fields are rejected', async () => {
  const cases = [
    { [branchRoute]: response({}, 200, { redirected: true }) },
    { [branchRoute]: response({}, 200, { url: 'https://example.com/' }) },
    { [branchRoute]: response({}, 200, { text: async () => 'not json' }) },
    { [branchRoute]: response({ ...commit, sha: 'main' }) },
    { [branchRoute]: response({ sha, commit: { committer: { date: 'bad' } } }) },
    { ['/contents/desktop/package.json?ref=' + sha]: response({ path: 'desktop/package.json', encoding: 'base64', content: '@invalid' }) },
    { ['/contents/desktop/package.json?ref=' + sha]: response(contents({ name: 'other', version: '0.2.5' })) },
    { ['/contents/desktop/package.json?ref=' + sha]: response(contents({ name: 'crew-desktop', version: 'latest', build })) },
    { ['/contents/desktop/package.json?ref=' + sha]: response(contents({ name: 'crew-desktop', version: '0.2.40' })) },
    { ['/contents/desktop/package.json?ref=' + sha]: response(contents({ name: 'crew-desktop', version: '0.2.40', build:{appId:'local.crew.desktop'} })) }
  ];
  for (const value of cases) await assert.rejects(checkLatest({ fetchImpl: fixture(value).fetchImpl }), /GitHub|Crew 版本/);
});

test('one deadline bounds the complete query, including a fetch that ignores abort', async () => {
  let signal;
  const started = Date.now();
  await assert.rejects(checkLatest({ timeoutMs: 15, fetchImpl: async (_url, options) => {
    signal = options.signal; return new Promise(() => {});
  } }), /超时/);
  assert.equal(signal.aborted, true); assert.ok(Date.now() - started < 1000);
  const f = fixture({ [branchRoute]: response(commit, 200, { text: async () => new Promise(() => {}) }) });
  await assert.rejects(checkLatest({ fetchImpl: f.fetchImpl, timeoutMs: 15 }), /超时/);
});

test('semantic comparison handles multi-digit and preview versions without lexical mistakes', () => {
  assert.equal(compareVersions('0.2.5', '0.1.2'), 1);
  assert.equal(compareVersions('0.10.0', '0.9.99'), 1);
  assert.equal(compareVersions('0.2.6', '0.2.6-beta.2'), 1);
  assert.equal(compareVersions('0.2.6-beta.2', '0.2.6-beta.11'), -1);
  assert.equal(compareVersions('0.2.6-1', '0.2.6-alpha'), -1);
  assert.equal(compareVersions('0.2.6-alpha', '0.2.6-alpha.1'), -1);
  assert.equal(compareVersions('v0.2.6+build1', '0.2.6+build2'), 0);
  for (const value of ['0.02.6', '0.2', 'latest', '0.2.6-01']) assert.throws(() => compareVersions(value, '0.2.6'), /格式无效/);
});
