import { test } from 'node:test';
import assert from 'node:assert/strict';
import { parseGoogleClientJson, validateCredentialJson } from './oauthHelpers.js';

test('parseGoogleClientJson handles GCP installed, web, and flat payloads', () => {
  const desktop = parseGoogleClientJson(JSON.stringify({
    installed: {
      client_id: '12345.apps.googleusercontent.com',
      client_secret: 'sec-desk',
      redirect_uris: ['http://localhost'],
    },
  }));
  assert.equal(desktop.kind, 'installed');
  assert.equal(desktop.client_id, '12345.apps.googleusercontent.com');
  assert.equal(desktop.client_secret, 'sec-desk');
  assert.equal(desktop.redirect_uri, 'http://localhost');

  const web = parseGoogleClientJson({
    web: {
      client_id: 'web-cid.apps.googleusercontent.com',
      redirect_uris: ['https://draw.example.com/api/oauth/drive/callback'],
    },
  });
  assert.equal(web.kind, 'web');
  assert.equal(web.redirect_uri, 'https://draw.example.com/api/oauth/drive/callback');

  assert.throws(() => parseGoogleClientJson('{"client_secret":"only"}'), /client_id/);
});

test('validateCredentialJson enforces client_id and refresh_token', () => {
  const ok = validateCredentialJson('{"client_id":"cid","refresh_token":"1//abc"}');
  assert.equal(ok.client_id, 'cid');
  assert.throws(() => validateCredentialJson('{"client_id":"cid"}'), /refresh_token/);
  assert.throws(() => validateCredentialJson('{not json'), /JSON/);
});
