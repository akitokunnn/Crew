'use strict';
// Read-only version discovery. The repository and all request paths are fixed.
const API = 'https://api.github.com/repos/akitokunnn/Crew';
const WEB = 'https://github.com/akitokunnn/Crew';
const BRANCH = 'rebuild/v2';
const APP_ID = require('./product.cjs').appId;
const VERSION = /^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)(?:-([0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*))?(?:\+[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?$/;

function parseVersion(value) {
  const match = typeof value === 'string' && VERSION.exec(value.replace(/^v/, ''));
  if (!match || match[4]?.split('.').some(part => /^\d+$/.test(part) && part.length > 1 && part[0] === '0')) {
    throw new Error('Crew 版本格式无效：' + String(value));
  }
  return { numbers: match.slice(1, 4).map(BigInt), prerelease: match[4]?.split('.') || [] };
}

function compareVersions(left, right) {
  const a = parseVersion(left), b = parseVersion(right);
  for (let index = 0; index < 3; index++) {
    if (a.numbers[index] !== b.numbers[index]) return a.numbers[index] < b.numbers[index] ? -1 : 1;
  }
  if (!a.prerelease.length || !b.prerelease.length) return a.prerelease.length === b.prerelease.length ? 0 : a.prerelease.length ? -1 : 1;
  for (let index = 0; index < Math.max(a.prerelease.length, b.prerelease.length); index++) {
    const x = a.prerelease[index], y = b.prerelease[index];
    if (x === undefined || y === undefined) return x === y ? 0 : x === undefined ? -1 : 1;
    if (x === y) continue;
    const numericX = /^\d+$/.test(x), numericY = /^\d+$/.test(y);
    if (numericX && numericY) return BigInt(x) < BigInt(y) ? -1 : 1;
    if (numericX !== numericY) return numericX ? -1 : 1;
    return x < y ? -1 : 1;
  }
  return 0;
}

function date(value, label) {
  if (typeof value !== 'string' || !Number.isFinite(Date.parse(value))) throw new Error('GitHub 返回的' + label + '无效');
  return value;
}

async function checkLatest({ fetchImpl = globalThis.fetch, timeoutMs = 10000, now = () => new Date().toISOString(), signal } = {}) {
  if (typeof fetchImpl !== 'function' || !Number.isSafeInteger(timeoutMs) || timeoutMs < 1 || timeoutMs > 10000) throw new TypeError('版本查询需要 fetch 和 1–10000 毫秒期限');
  signal?.throwIfAborted();
  const controller = new AbortController();
  let rejectCancelled;
  const cancelled = new Promise((_resolve,reject) => { rejectCancelled=reject; });
  const onAbort = () => { controller.abort(signal.reason); rejectCancelled(signal.reason); };
  signal?.addEventListener('abort',onAbort,{ once:true });
  let timer;
  const deadline = new Promise((_resolve, reject) => {
    timer = setTimeout(() => {
      const error = new Error('查询 Crew 最新版本超时（' + Math.ceil(timeoutMs / 1000) + ' 秒）');
      reject(error); controller.abort(error);
    }, timeoutMs);
  });
  async function get(route) {
    controller.signal.throwIfAborted();
    const url = API + route;
    let response;
    try {
      response = await fetchImpl(url, { redirect: 'error', credentials: 'omit', signal: controller.signal,
        headers: { Accept: 'application/vnd.github+json', 'X-GitHub-Api-Version': '2022-11-28' } });
    } catch (error) {
      controller.signal.throwIfAborted();
      throw new Error('GitHub 版本查询连接失败：' + error.message, { cause: error });
    }
    controller.signal.throwIfAborted();
    if (response.redirected || response.url && response.url !== url) throw new Error('GitHub 版本查询发生了未允许的跳转');
    if (!response.ok) {
      if ([403, 429].includes(response.status)) throw new Error('GitHub 版本查询受限（HTTP ' + response.status + '），请稍后重试');
      throw new Error('GitHub 版本查询失败（HTTP ' + response.status + '）');
    }
    const text = await response.text();
    controller.signal.throwIfAborted();
    if (text.length > 1024 * 1024) throw new Error('GitHub 版本查询返回内容过大');
    try { return JSON.parse(text); }
    catch (error) { throw new Error('GitHub 版本查询没有返回有效 JSON', { cause: error }); }
  }
  const work = (async () => {
    // The repository's latest release and main belong to V1. V2 only follows
    // its explicit development branch until it has a separate release feed.
    const commit = await get('/commits/' + encodeURIComponent(BRANCH));
    if (!commit || typeof commit.sha !== 'string' || !/^[0-9a-f]{40}$/.test(commit.sha)) throw new Error('GitHub V2 分支提交信息无效');
    const committed_at = date(commit.commit?.committer?.date, '提交时间');
    const contents = await get('/contents/desktop/package.json?ref=' + commit.sha);
    if (!contents || contents.path !== 'desktop/package.json' || contents.encoding !== 'base64' || typeof contents.content !== 'string') throw new Error('GitHub V2 分支版本文件无效');
    const encoded = contents.content.replace(/\s/g, '');
    if (!encoded || encoded.length > 131072 || !/^(?:[A-Za-z0-9+/]{4})*(?:[A-Za-z0-9+/]{2}==|[A-Za-z0-9+/]{3}=)?$/.test(encoded)) throw new Error('GitHub V2 分支版本文件编码无效');
    let packageJson;
    try { packageJson = JSON.parse(new TextDecoder('utf-8', { fatal: true }).decode(Buffer.from(encoded, 'base64'))); }
    catch (error) { throw new Error('GitHub V2 分支版本文件不是有效 JSON', { cause: error }); }
    if (!packageJson || packageJson.name !== 'crew-desktop' || typeof packageJson.version !== 'string') throw new Error('GitHub V2 分支版本文件缺少 Crew 版本');
    if (packageJson.build?.appId !== APP_ID) throw new Error('GitHub rebuild/v2 尚未发布匹配的 Crew V2 源码；不会使用 V1 更新');
    parseVersion(packageJson.version);
    return { release: null, branch: BRANCH, main: { version: packageJson.version, sha: commit.sha, branch: BRANCH,
      url: WEB + '/blob/' + commit.sha + '/desktop/package.json', committed_at }, checked_at: now() };
  })();
  try { return await Promise.race([work, deadline, cancelled]); }
  finally { clearTimeout(timer); signal?.removeEventListener('abort',onAbort); }
}

module.exports = { checkLatest, compareVersions };
