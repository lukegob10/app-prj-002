import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { test } from 'node:test';
import { runInNewContext } from 'node:vm';

const html = readFileSync(new URL('../src/index.html', import.meta.url), 'utf8');
const bootstrap = html.match(/<script>([\s\S]*?)<\/script>/)[1];

function openPage(address) {
  const url = new URL(address);
  let redirectedTo;
  runInNewContext(bootstrap, {
    URL,
    location: {
      protocol: url.protocol, hostname: url.hostname, href: url.href,
      replace: value => { redirectedTo = value; },
    },
    localStorage: { getItem: () => null },
    matchMedia: () => ({ matches: false }),
    document: { documentElement: { dataset: {} }, querySelector: () => ({}) },
  });
  return redirectedTo;
}

test('numeric local login uses the configured localhost origin', () => {
  assert.equal(openPage('http://127.0.0.1:4200/login'), 'http://localhost:4200/login');
});

test('redirect preserves instance port, project route, query and fragment', () => {
  assert.equal(
    openPage('http://127.0.0.1:4201/projects/example?view=manage&tab=data#records'),
    'http://localhost:4201/projects/example?view=manage&tab=data#records',
  );
});

test('IPv6 loopback uses the same canonical origin', () => {
  assert.equal(openPage('http://[::1]:4200/login'), 'http://localhost:4200/login');
});

test('canonical URLs do not loop and remote or HTTPS origins are unchanged', () => {
  for (const address of [
    'http://localhost:4200/login', 'https://agora.example/login',
    'https://127.0.0.1:4200/login', 'http://127.0.0.1.example:4200/login',
  ]) assert.equal(openPage(address), undefined, address);
});

test('document base keeps module entrypoints rooted on nested project routes', () => {
  const baseHref = html.match(/<base href="([^"]+)"\s*\/?\s*>/)?.[1];
  assert.equal(baseHref, '/', 'deep links need an origin-rooted base for relative module URLs');
  const deepLink = new URL('http://localhost:4200/projects/example?view=manage&tab=data');
  assert.equal(new URL('main.js', new URL(baseHref, deepLink)).pathname, '/main.js');
});
