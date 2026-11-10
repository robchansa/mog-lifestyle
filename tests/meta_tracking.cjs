/* Behavioral privacy tests. No browser or network requests are made. */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
const source = fs.readFileSync(path.join(__dirname, '../app/static/js/meta.js'), 'utf8');

function boot(options = {}) {
  const handlers = {}, docHandlers = {}, storage = {...options.storage}, scripts = [], cookies = [];
  const accept = {addEventListener: (_, fn) => {handlers.accept = fn;}};
  const reject = {addEventListener: (_, fn) => {handlers.reject = fn;}};
  const paragraph = {};
  const panel = {hidden: true, querySelector: s => s === 'p' ? paragraph : s.includes('accept') ? accept : reject};
  const google = {hidden: options.googleHidden !== false};
  const document = {
    readyState: options.readyState || 'complete',
    referrer: options.referrer || '',
    querySelector: s => s.includes('data-meta') ? panel : google,
    createElement: () => ({}),
    head: {appendChild: e => scripts.push(e)},
    addEventListener: (name, fn) => {docHandlers[name] = fn;}
  };
  Object.defineProperty(document, 'cookie', {set: v => cookies.push(v)});
  const location = {hostname: 'moglifestyle.fit', origin: 'https://moglifestyle.fit', pathname: '/', search: '', reload() {this.reloaded = true;}, ...options.location};
  const window = {location, localStorage: {getItem: k => storage[k] || null, setItem: (k,v) => {storage[k] = v;}}};
  let mutation;
  const context = {window, document, navigator: options.navigator || {}, URL, URLSearchParams,
    MutationObserver: class {constructor(fn) {mutation = fn;} observe() {}}};
  vm.runInNewContext(source, context);
  if (options.readyState === 'loading') {
    // Simulate Google's earlier DOMContentLoaded listener displaying its UI.
    google.hidden = false;
    assert.equal(panel.hidden, true);
    docHandlers.DOMContentLoaded();
  }
  const event = (id = 'a'.repeat(32)) => docHandlers['mog:preorder-signup']({detail: {eventId: id, email: 'private@example.com'}});
  return {window, panel, accept, google, handlers, scripts, cookies, event, storage,
    mutation: () => mutation(), calls: () => Array.from(window.fbq?.queue || [], a => Array.from(a))};
}

let x = boot();
assert.equal(x.scripts.length, 0);
assert.equal(x.panel.hidden, false);
x.event();
assert.equal(x.scripts.length, 0);
x.handlers.accept();
assert.equal(x.scripts.length, 1);
assert.equal(x.scripts[0].src, 'https://connect.facebook.net/en_US/fbevents.js');
assert.deepEqual(x.calls().slice(0,4), [['consent','grant'],['set','autoConfig',false,'1439220394903801'],['init','1439220394903801'],['track','PageView']]);
x.handlers.accept();
assert.equal(x.scripts.length, 1);
x.event(); x.event(); x.event('private@example.com');
assert.equal(x.calls().filter(c => c[1] === 'Lead').length, 1);
assert(!JSON.stringify(x.calls()).includes('private@example.com'));
x.handlers.reject();
assert.equal(x.window.location.reloaded, true);
assert.equal(x.calls().at(-1)[1], 'revoke');
const count = x.calls().length;
x.event('b'.repeat(32));
assert.equal(x.calls().length, count);
assert(x.cookies.some(c => c.startsWith('_fbp=;')));

for (const options of [
  {storage: {mog_analytics_consent: 'yes'}},
  {storage: {mog_meta_consent_v1: 'no'}},
  {navigator: {globalPrivacyControl: true}},
  {navigator: {doNotTrack: '1'}}
]) {
  x = boot(options); assert.equal(x.scripts.length, 0);
}
for (const options of [
  {location: {pathname: '/admin'}},
  {location: {hostname: 'localhost'}},
  {location: {search: '?email=private@example.com'}},
  {referrer: 'https://moglifestyle.fit/admin/customers'},
  {referrer: 'https://moglifestyle.fit/?email=private@example.com'},
  {navigator: {globalPrivacyControl: true}},
  {navigator: {doNotTrack: '1'}}
]) {
  x = boot({...options, storage: {mog_meta_consent_v1: 'yes'}});
  assert.equal(x.scripts.length, 0);
}
x = boot({googleHidden: false});
assert.equal(x.panel.hidden, true);
x.google.hidden = true; x.mutation();
assert.equal(x.panel.hidden, false);
x = boot({readyState: 'loading'});
assert.equal(x.panel.hidden, true);
x.google.hidden = true; x.mutation();
assert.equal(x.panel.hidden, false);
x.google.hidden = false; x.mutation();
assert.equal(x.panel.hidden, true);
x = boot({storage: {mog_meta_consent_v1: 'yes'}, location: {search: '?utm_source=instagram&fbclid=example'}});
assert.equal(x.scripts.length, 1);
x = boot({storage: {mog_meta_consent_v1: 'no'}, location: {search: '?ad_settings=1'}});
assert.equal(x.panel.hidden, false);
console.log('Meta tracking behavioral tests passed');
