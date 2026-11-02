// End-to-end run against the docker compose stack (nginx on :8080, real MySQL/MongoDB, the C2/C3
// stubs with the demo generator and the classifier worker, MailHog). Runs only when
// E2E_BASE_URL is set, e.g.  E2E_BASE_URL=http://localhost:8080 npx playwright test
// MailHog's API is E2E_MAILHOG_URL (default http://localhost:8025).
import { expect, test, type Page } from '@playwright/test';

const PASSWORD = 'Passw0rd!demo';
const MAILHOG = process.env.E2E_MAILHOG_URL ?? 'http://localhost:8025';

test.skip(!process.env.E2E_BASE_URL, 'needs the running stack (set E2E_BASE_URL)');

async function login(page: Page, email: string) {
  await page.goto('/login');
  await page.getByTestId('login-email').fill(email);
  await page.getByTestId('login-password').fill(PASSWORD);
  await page.getByTestId('login-submit').click();
  await expect(page.getByTestId('page-title')).toBeVisible();
}

async function logout(page: Page) {
  await page.getByTestId('user-menu').click();
  await page.getByTestId('logout').click();
  await expect(page.getByTestId('login-form')).toBeVisible();
}

/** Toasts queue up (4 s each, and live alerts add their own), so look for the one we expect. */
async function expectToast(page: Page, text: string) {
  await expect(page.getByTestId('toast').filter({ hasText: text })).toBeVisible({ timeout: 30_000 });
}

/** Screen position of every car marker, keyed by the car code in its label. */
async function markerPositions(page: Page): Promise<Record<string, string>> {
  return page.getByTestId('map-card').locator('.scicp-car-marker').evaluateAll((nodes) => {
    const out: Record<string, string> = {};
    for (const node of nodes) {
      const code = (node.textContent ?? '').match(/AV-\d+/)?.[0];
      if (code) out[code] = (node as HTMLElement).style.transform;
    }
    return out;
  });
}

test.describe('SCICP stack (docker compose)', () => {
  test('owner: overview with KPIs and map, full map, subscriptions save, logout', async ({ page }) => {
    await login(page, 'anh@example.com');
    await expect(page.getByTestId('page-title')).toContainText('Overview - Nguyen household');
    await expect(page.getByTestId('kpi-row').locator('[data-kpi]')).toHaveCount(5);
    await expect(page.getByTestId('kpi-vehicles').getByTestId('kpi-value')).toHaveText('1');
    const mapCard = page.getByTestId('map-card');
    await expect(mapCard.locator('.scicp-car-marker')).toHaveCount(1);
    await expect(mapCard.locator('.scicp-car-marker')).toContainText('AV-01');

    await page.getByTestId('menu-map').click();
    await expect(page).toHaveURL(/\/map/);
    await expect(page.getByTestId('car-list')).toContainText('AV-01');

    await page.getByTestId('menu-subscriptions').click();
    const card = page.getByTestId('subscription-card-AV-01');
    await card.getByTestId('service-row-SAFETY').getByRole('checkbox', { name: 'SAFETY enabled' }).click();
    await card.getByTestId('subscription-save').click();
    await expectToast(page, 'Settings saved');
    await logout(page);
  });

  test('fleet manager: cars move on the map; acknowledge updates the alert monitor live', async ({ page, browser }) => {
    await login(page, 'maria@bayshuttle.example');
    await page.getByTestId('menu-map').click();
    await expect(page).toHaveURL(/\/map/);
    const markers = page.getByTestId('map-card').locator('.scicp-car-marker');
    // AV-10..AV-13 have telemetry (AV-13 stopped 14 min ago); AV-14 never sent any.
    await expect(markers).toHaveCount(4);
    await expect(page.getByTestId('car-list')).toContainText('AV-14');
    const first = await markerPositions(page);
    await page.waitForTimeout(20_000);
    const second = await markerPositions(page);
    const moved = ['AV-10', 'AV-11', 'AV-12'].filter((code) => first[code] && second[code] && first[code] !== second[code]);
    expect(moved.length, `markers that moved: ${moved.join(', ')}`).toBeGreaterThanOrEqual(2);
    expect(second['AV-13']).toBe(first['AV-13']); // offline: no new samples

    // A second session of the same tenant watches the alert monitor.
    const other = await browser.newContext();
    const watcher = await other.newPage();
    await login(watcher, 'maria@bayshuttle.example');
    await watcher.getByTestId('menu-alerts').click();
    await expect(watcher.getByTestId('alerts-table').locator('tbody tr')).not.toHaveCount(0);

    await page.getByTestId('menu-alerts').click();
    const table = page.getByTestId('alerts-table');
    await expect(table.locator('tbody tr').first()).toBeVisible();
    const row = table.locator('tbody tr').filter({ has: page.getByRole('button', { name: 'Acknowledge' }) }).first();
    await expect(row, 'a NEW alert of Bay Shuttle to acknowledge').toBeVisible();
    const alertId = await row.getAttribute('data-row-key');
    expect(alertId).toBeTruthy();
    await row.getByRole('button', { name: 'Acknowledge' }).click();
    await expect(table.locator(`tr[data-row-key="${alertId}"]`).locator('[data-status="ACKNOWLEDGED"]')).toBeVisible();
    // The other session gets alert.updated over the WebSocket and refetches the row.
    await expect(watcher.getByTestId('alerts-table').locator(`tr[data-row-key="${alertId}"]`).locator('[data-status="ACKNOWLEDGED"]')).toBeVisible({ timeout: 20_000 });
    await other.close();
    await logout(page);
  });

  test('admin: tenant, invitation mail, model upload, activation and rollback with deployment', async ({ page, request }) => {
    const stamp = Date.now() % 1_000_000;
    const tenantName = `E2E Fleet ${stamp}`;
    const email = `e2e-${stamp}@example.com`;
    await login(page, 'admin@scicp.example');

    // Tenant
    await page.getByTestId('menu-tenants').click();
    await page.getByTestId('new-tenant').click();
    await page.getByTestId('tenant-name').fill(tenantName);
    await page.getByTestId('tenant-create').click();
    await expectToast(page, `Tenant ${tenantName} created`);

    // Invitation into the new tenant; the mail reaches MailHog
    await page.getByTestId('menu-users').click();
    await page.getByTestId('invite-user').click();
    const dialog = page.getByRole('dialog');
    await dialog.getByTestId('invite-email').fill(email);
    await dialog.getByTestId('invite-name').fill('E2E Manager');
    await dialog.getByRole('combobox', { name: 'Tenant' }).click();
    await page.getByRole('option', { name: tenantName }).click();
    await dialog.getByTestId('invite-send').click();
    await expectToast(page, `Invitation sent to ${email}`);
    await expect
      .poll(async () => {
        const response = await request.get(`${MAILHOG}/api/v2/messages?limit=50`);
        if (!response.ok()) return 'mailhog unreachable';
        const body = (await response.json()) as { items: Array<{ Raw: { To: string[] } }> };
        return body.items.some((m) => m.Raw.To.some((to) => to.includes(email))) ? 'delivered' : 'missing';
      }, { timeout: 20_000 })
      .toBe('delivered');

    // Models: upload a new version through the presigned URL
    await page.getByTestId('menu-models').click();
    await expect(page.getByTestId('models-table')).toContainText('sound-classifier');
    const deployment = page.getByTestId('deployment-card');
    await expect(deployment.getByTestId('worker-state').locator('[data-status="READY"]')).toBeVisible({ timeout: 40_000 });
    const before = (await deployment.getByTestId('worker-version').first().textContent())?.trim().split(' ')[0] ?? '';
    expect(before).toMatch(/^\d+\.\d+\.\d+$/);

    const version = `4.${stamp}.0`;
    await page.getByTestId('upload-new-version').click();
    await page.getByTestId('upload-version').fill(version);
    await page.getByTestId('upload-file').setInputFiles({ name: 'model.pt', mimeType: 'application/octet-stream', buffer: Buffer.from(`e2e dummy weights ${version}\n`.repeat(2000)) });
    await page.getByTestId('upload-macro-f1').fill('0.87');
    await page.getByTestId('upload-submit').click();
    await expectToast(page, `Version ${version} registered`);
    const panel = page.getByTestId('version-panel');
    await expect(panel).toContainText(`Version ${version}`);
    await expect(panel.locator('[data-status="REGISTERED"]')).toBeVisible();

    // Watch the deployment card for LOADING before activating (the state lasts about 2 s).
    await page.evaluate(() => {
      const seen: string[] = [];
      (window as unknown as { __workerStates: string[] }).__workerStates = seen;
      const card = document.querySelector('[data-testid="deployment-card"]');
      if (!card) return;
      new MutationObserver(() => {
        card.querySelectorAll('[data-testid="worker-state"] [data-status]').forEach((chip) => seen.push(chip.getAttribute('data-status') ?? ''));
      }).observe(card, { subtree: true, childList: true, attributes: true });
    });

    // Activate (BR-10 confirm dialog shows both macro-F1 values)
    await page.getByTestId('set-active').click();
    const confirm = page.getByRole('dialog');
    await expect(confirm).toContainText('BR-10 passes');
    await expect(confirm).toContainText(`${version} (macro-F1 0.87)`);
    await confirm.getByTestId('confirm-button').click();
    await expectToast(page, `Version ${version} is active`);
    await expect(page.getByTestId('version-table').locator(`tr[data-row-key]`).filter({ hasText: version }).locator('[data-status="ACTIVE"]')).toBeVisible();
    await expect(deployment.getByTestId('worker-version').first()).toHaveText(version, { timeout: 20_000 });
    await expect(deployment.getByTestId('worker-state').locator('[data-status="READY"]')).toBeVisible({ timeout: 20_000 });
    await expect(deployment.getByTestId('worker-sha-verified')).toHaveAttribute('data-verified', 'yes');
    const states = await page.evaluate(() => (window as unknown as { __workerStates: string[] }).__workerStates);
    expect(states, 'the card showed LOADING before READY').toContain('LOADING');

    // Roll back: the worker returns to the previous version
    await page.getByTestId('rollback').click();
    await page.getByRole('dialog').getByTestId('confirm-button').click();
    await expectToast(page, `Rolled back: version ${before} is active`);
    await expect(deployment.getByTestId('worker-version').first()).toHaveText(before, { timeout: 20_000 });
    await expect(deployment.getByTestId('worker-state').locator('[data-status="READY"]')).toBeVisible({ timeout: 20_000 });
    await logout(page);
  });
});
