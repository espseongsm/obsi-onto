const {test, expect} = require('@playwright/test');

test('untrusted website cannot read the token or mutate local settings', async ({page, request}) => {
  const token = (await (await request.get('/api/session')).json()).token;
  await page.goto('http://127.0.0.1:8772');
  const result = await page.evaluate(async token => {
    const read = await fetch('http://127.0.0.1:8771/api/session').then(
      () => 'readable', () => 'blocked');
    const mutation = await fetch('http://127.0.0.1:8771/api/history/retention', {
      method: 'POST', headers: {'Content-Type': 'application/json', 'X-Obsi-Token': token},
      body: JSON.stringify({days: 30}),
    }).then(() => 'accepted', () => 'blocked');
    return {read, mutation};
  }, token);
  expect(result).toEqual({read: 'blocked', mutation: 'blocked'});
  const status = await request.get('/api/status', {headers: {'X-Obsi-Token': token}});
  expect((await status.json()).history.retention_days).toBe(0);
  expect((await request.get('/api/status')).status()).toBe(403);
});

test('source text remains text, and browser enforces the app CSP', async ({page}) => {
  await page.goto('/');
  await page.getByRole('textbox', {name: /What evidence/}).fill('Security fixture');
  await page.locator('#question-form').getByRole('button', {name: 'Find evidence'}).click();
  await expect(page.locator('#chat-thread .chat-assistant')).toBeVisible();
  await expect(page.locator('#query-progress')).toContainText('Complete');
  await page.locator('#chat-thread .chat-sources summary').first().click();
  await page.locator('#chat-thread .source-tile').filter({hasText: 'window.__obsiXSS'}).first().click();
  await expect(page.locator('#source-detail-dialog')).toBeVisible();
  await expect(page.locator('#source-detail-dialog')).toContainText('window.__obsiXSS');
  expect(await page.evaluate(() => window.__obsiXSS || null)).toBeNull();
  expect(await page.locator('#source-detail-dialog img').count()).toBe(0);
  await page.evaluate(() => {
    const script = document.createElement('script');
    script.textContent = 'window.__obsiInlineScript = 1'; document.body.append(script);
  });
  expect(await page.evaluate(() => window.__obsiInlineScript || null)).toBeNull();
});

test('model destination guidance hides private URL details', async ({page}) => {
  await page.route('**/api/status', async route => {
    const response = await route.fetch();
    const data = await response.json();
    data.transmission.generation = {enabled: true, external: true,
      recipient: 'https://provider.example', scope: 'Question, selected passages and recent turns.'};
    data.transmission.suggestions.enabled = false;
    await route.fulfill({response, json: data});
  });
  await page.goto('/');
  await expect(page.locator('#transmission-notice')).toContainText('https://provider.example');
  await page.getByRole('button', {name: 'Vault settings', exact: false}).click();
  await expect(page.locator('#transmission-info')).toContainText('Question, selected passages');
  await expect(page.locator('#transmission-info')).toContainText('Local processing · This computer');
  await expect(page.locator('#transmission-info')).toContainText('Model transmission is off.');
});

test('history deletion is opt-in, keeps notes and recent answers', async ({page}) => {
  await page.goto('/');
  await page.getByRole('button', {name: 'Vault settings', exact: false}).click();
  await expect(page.locator('#retention-days')).toHaveValue('0');
  page.once('dialog', dialog => dialog.dismiss());
  await page.getByRole('button', {name: 'Delete older history', exact: true}).click();
  await expect(page.locator('#storage-usage')).toContainText('saved answers');
  const before = await page.evaluate(async () => {
    const session = await (await fetch('/api/session')).json();
    return (await fetch('/api/status', {headers: {'X-Obsi-Token': session.token}})).json();
  });
  expect(before.history.runs).toBeGreaterThanOrEqual(1);
  page.once('dialog', dialog => dialog.accept());
  await page.getByRole('button', {name: 'Delete older history', exact: true}).click();
  await expect(page.locator('#toast')).toContainText('Deleted 1 saved answers');
  await expect(page.locator('#retention-days')).toHaveValue('0');
  const after = await page.evaluate(async () => {
    const session = await (await fetch('/api/session')).json();
    return (await fetch('/api/status', {headers: {'X-Obsi-Token': session.token}})).json();
  });
  expect(after.counts.notes).toBe(before.counts.notes);
  expect(after.history.runs).toBe(before.history.runs - 1);
  expect(after.vault).toBe(before.vault);
});
