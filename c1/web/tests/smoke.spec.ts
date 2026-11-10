import { expect, test, type Page } from '@playwright/test';

const PASSWORD = 'Passw0rd!demo';

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

test.describe('SCICP C1 smoke', () => {
  test('wrong password shows the generic 401 error', async ({ page }) => {
    await page.goto('/login');
    await page.getByTestId('login-email').fill('anh@example.com');
    await page.getByTestId('login-password').fill('not-the-password');
    await page.getByTestId('login-submit').click();
    const error = page.getByTestId('login-error');
    await expect(error).toBeVisible();
    await expect(error).toHaveAttribute('data-kind', '401');
    await expect(error).toContainText('Invalid email or password');
  });

  test('owner: overview, map, subscriptions; admin: tenants and models', async ({ page }) => {
    // Owner of tenant A
    await login(page, 'anh@example.com');
    await expect(page).toHaveURL(/\/overview$/);
    await expect(page.getByTestId('page-title')).toContainText('Overview - Nguyen household');
    await expect(page.getByTestId('user-name')).toContainText('Anh Nguyen');

    // KPI tiles with the seed values of tenant A
    const kpis = page.getByTestId('kpi-row').locator('[data-kpi]');
    await expect(kpis).toHaveCount(5);
    await expect(page.getByTestId('kpi-vehicles').getByTestId('kpi-value')).toHaveText('1');
    await expect(page.getByTestId('kpi-open-alerts').getByTestId('kpi-value')).not.toHaveText('');

    // The map renders one car marker (AV-01) inside the map card
    const mapCard = page.getByTestId('map-card');
    await expect(mapCard.locator('.leaflet-container')).toBeVisible();
    await expect(mapCard.locator('.scicp-car-marker')).toHaveCount(1);
    await expect(mapCard.locator('.scicp-car-marker')).toContainText('AV-01');

    // Vehicle card and recent alerts (alert 20482 is NEW and can be acknowledged)
    await expect(page.getByTestId('vehicle-card')).toHaveCount(1);
    await expect(page.getByTestId('recent-alerts')).toContainText('ACCIDENT');
    await expect(page.getByTestId('subscriptions-panel')).toContainText('AV-01');

    // Full map page
    await page.getByTestId('open-full-map').click();
    await expect(page).toHaveURL(/\/map/);
    await expect(page.getByTestId('page-title')).toContainText('Vehicle map');
    await expect(page.getByTestId('car-list')).toContainText('AV-01');
    await expect(page.getByTestId('map-card').locator('.scicp-car-marker')).toHaveCount(1);
    await page.getByTestId('car-list').getByText('AV-01').first().click();
    await expect(page.getByTestId('car-detail')).toContainText('AV-01');
    await expect(page.getByTestId('car-detail')).toContainText('open alerts');

    // Subscriptions page: one card per vehicle with the six services
    await page.getByTestId('menu-subscriptions').click();
    await expect(page).toHaveURL(/\/subscriptions$/);
    const card = page.getByTestId('subscription-card-AV-01');
    await expect(card).toBeVisible();
    await expect(card.getByTestId('service-row-SECURITY')).toBeVisible();
    await expect(card.getByTestId('service-row-PASSENGER')).toContainText('design only');
    await expect(card.getByTestId('subscription-save')).toBeDisabled();
    // Flip SAFETY and save: a toast confirms the PUT
    await card.getByTestId('service-row-SAFETY').getByRole('checkbox', { name: 'SAFETY enabled' }).click();
    await expect(card.getByTestId('subscription-save')).toBeEnabled();
    await card.getByTestId('subscription-save').click();
    await expect(page.getByTestId('toast')).toContainText('Settings saved');

    // Reports page renders the filter card and runs a report
    await page.getByTestId('menu-reports').click();
    await page.getByTestId('run-report').click();
    await expect(page.getByTestId('report-rows')).toBeVisible();

    await logout(page);

    // Administrator
    await login(page, 'admin@scicp.example');
    await expect(page.getByTestId('tenant-selector')).toBeVisible();
    await page.getByTestId('menu-tenants').click();
    await expect(page).toHaveURL(/\/admin\/tenants$/);
    const tenants = page.getByTestId('tenants-table');
    await expect(tenants).toContainText('Nguyen household');
    await expect(tenants).toContainText('Bay Shuttle LLC');
    await tenants.getByText('Bay Shuttle LLC').click();
    await expect(page.getByTestId('tenant-panel')).toContainText('Tenant: Bay Shuttle LLC');
    await expect(page.getByTestId('suspend-tenant')).toBeVisible();

    // Users and alert types tabs
    await page.getByTestId('tab-users').click();
    await expect(page.getByTestId('users-table')).toContainText('admin@scicp.example');
    await page.getByTestId('tab-alert-types').click();
    await expect(page.getByTestId('alert-types-table')).toContainText('ACCIDENT');

    // Models: list, versions, selected version panel with the metrics table
    await page.getByTestId('menu-models').click();
    await expect(page).toHaveURL(/\/models$/);
    await expect(page.getByTestId('models-table')).toContainText('sound-classifier');
    const versions = page.getByTestId('version-table');
    await expect(versions).toContainText('1.1.0');
    await expect(versions).toContainText('1.2.0');
    await expect(versions.locator('[data-status="ACTIVE"]')).toHaveCount(1);
    // The page preselects the newest non-active version; pick 1.2.0 explicitly so that a
    // registry with later uploads (a reused stack) gives the same panel.
    await versions.locator('tbody tr').filter({ hasText: /^1\.2\.0/ }).click();
    await expect(page.getByTestId('version-panel')).toContainText('Version 1.2.0');
    await expect(page.getByTestId('metrics-table')).toContainText('siren');
    await expect(page.getByTestId('set-active')).toBeEnabled();
    // The confirm dialog shows both macro-F1 values (BR-10)
    await page.getByTestId('set-active').click();
    await expect(page.getByRole('dialog')).toContainText('1.1.0 (macro-F1 0.68)');
    await expect(page.getByRole('dialog')).toContainText('1.2.0 (macro-F1 0.99)');
    await page.getByRole('dialog').getByRole('button', { name: 'Cancel' }).click();

    // Staff overview for the selected tenant shows the KPI row (degraded without the stubs)
    await page.getByTestId('menu-overview').click();
    await expect(page.getByTestId('kpi-row')).toBeVisible();

    await logout(page);
  });
});
