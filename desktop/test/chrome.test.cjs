'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const chrome = require('../src/chrome.cjs');

test('desktop chrome is restricted to the exact local home or trusted application root', () => {
  const home = 'file:///C:/Crew/home.html', origin = 'http://127.0.0.1:1234';
  assert.equal(chrome.pageKind(home, home, origin), 'home');
  assert.equal(chrome.pageKind('file:///C:/Crew/error.html',home,origin,'file:///C:/Crew/error.html'),'error');
  assert.equal(chrome.pageKind(origin + '/?project=one#todos', home, origin), 'workspace');
  for (const url of ['file:///C:/Crew/other.html', home+'?other=1', origin+'/api/trust/report', origin+'/preview.html',
    'http://127.0.0.1:1235/', 'https://127.0.0.1:1234/', 'http://user@127.0.0.1:1234/', 'https://example.com/', 'bad']) {
    assert.equal(chrome.pageKind(url, home, origin), null, url);
  }
  const options = chrome.windowOptions('win32');
  assert.equal(options.titleBarStyle, 'hidden'); assert.equal(options.titleBarOverlay.height, 36);
  assert(options.closable && options.minimizable && options.maximizable && options.resizable);
});
