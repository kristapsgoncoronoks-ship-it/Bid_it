// Real browser actions against the private pilot origin; no page.route mocks.
import { chromium } from '../../frontend/node_modules/playwright/index.mjs';
import { readFileSync, writeFileSync } from 'node:fs';
import { createHash } from 'node:crypto';
import { execFileSync } from 'node:child_process';

const statePath = process.argv[2];
if (!statePath) throw new Error('Pass the private state.json path from controlled_finance.py');
const state = JSON.parse(readFileSync(statePath, 'utf8'));
const browser = await chromium.launch({ executablePath: process.env.PW_CHROMIUM_PATH || '/usr/bin/chromium', headless: true });
const contexts = [];
const errors = [];
const report = { synthetic_only: true, browser_mocks: false, actions: [] };
function save(name, data) { writeFileSync(`${state.output}/${name}`, JSON.stringify(data, null, 2), { mode: 0o600 }); }
async function actor(token) {
  const context = await browser.newContext({ acceptDownloads: true });contexts.push(context);
  await context.addInitScript(value => localStorage.setItem('invoiceiq_token', value), token);
  const page = await context.newPage();page.on('pageerror', error => errors.push(error.name));
  async function api(method, path, body, expected = 200, headers = {}) {
    const response = await context.request.fetch(state.base + '/api/v1' + path, { method, headers: { Authorization: `Bearer ${token}`, ...headers }, ...(body === undefined ? {} : { data: body }) });
    if (response.status() !== expected) {
      save('browser-error.json', { action: method + ' ' + path, status: response.status(), detail: (await response.text()).slice(0, 2000) });
      throw new Error('Pilot API action failed; inspect the private browser-error.json');
    }
    return response.json();
  }
  return { context, page, api };
}
try {
  const owner = await actor(state.owner_token);
  await owner.page.goto(`${state.base}/captures/${state.capture_id}`);
  const createdResponse = owner.page.waitForResponse(r => r.url().endsWith('/api/v1/invoices') && r.request().method() === 'POST');
  await owner.page.getByRole('button', { name: 'Confirm & create invoice' }).click();
  const created = await createdResponse;
  if (created.status() !== 201) throw new Error('Capture confirmation did not create an invoice');
  const ap = await created.json();state.ap_id = ap.id;
  report.actions.push('Browser confirmed real worker capture into received invoice');
  let review = await owner.api('GET', `/invoices/${ap.id}/review`);
  if (!review.reconciliation.balanced) throw new Error('Captured source does not reconcile');
  review = await owner.api('PATCH', `/invoices/${ap.id}/review`, { version: review.version, legal_entity_id: state.entity_id });
  await owner.page.goto(`${state.base}/invoices/${ap.id}/review`);
  const submitted = owner.page.waitForResponse(r => r.url().endsWith(`/invoices/${ap.id}/submit`));
  await owner.page.getByRole('button', { name: 'Submit for approval', exact: true }).click();
  if ((await submitted).status() !== 200) throw new Error('Submission failed');
  const approver = await actor(state.approver_token);
  await approver.page.goto(`${state.base}/invoices/${ap.id}/review`);
  const approval = approver.page.waitForResponse(r => r.url().endsWith(`/invoices/${ap.id}/approve`));
  await approver.page.getByRole('button', { name: 'Approve', exact: true }).click();
  const approved = await approval;
  if (approved.status() !== 200 || (await approved.json()).workflow_state !== 'approved') throw new Error('Separate actor approval failed');
  const locked = await owner.api('GET', `/invoices/${ap.id}/review`);
  if (!locked.locked) throw new Error('Approved source not locked');
  await owner.api('PATCH', `/invoices/${ap.id}/review`, { version: locked.version, notes: 'Synthetic forbidden edit' }, 409);
  report.actions.push('Browser submit and separate-user approval persisted; approved edit refused');

  const invoicePayload = { buyer_name: 'Synthetic pilot customer', issue_date: '2026-10-16', currency: 'EUR', lines: [{ description: 'Synthetic service', quantity: '1', unit_price: '100.00', vat_rate: '0' }] };
  let issued = await owner.api('POST', '/issued', invoicePayload, 201, { 'Idempotency-Key': 'synthetic-pilot-issue-command' });
  const replay = await owner.api('POST', '/issued', invoicePayload, 201, { 'Idempotency-Key': 'synthetic-pilot-issue-command' });
  if (replay.id !== issued.id) throw new Error('Issue replay created another legal document');
  const creditPayload = { expected_credited_total: '0.00', reason: 'Synthetic arithmetic test only', lines: [{ description: 'Synthetic partial credit', quantity: '1', unit_price: '10.00', vat_rate: '0' }] };
  const credit = await owner.api('POST', `/issued/${issued.id}/credit-note`, creditPayload, 201, { 'Idempotency-Key': 'synthetic-pilot-credit-command' });
  const creditReplay = await owner.api('POST', `/issued/${issued.id}/credit-note`, creditPayload, 200, { 'Idempotency-Key': 'synthetic-pilot-credit-command' });
  if (credit.id !== creditReplay.id) throw new Error('Credit replay duplicated a legal note');
  state.credit_id = credit.id;state.credit_payload = creditPayload;
  issued = await owner.api('GET', `/issued/${issued.id}`);
  if (issued.outstanding !== '90.00') throw new Error('Credit did not reduce effective balance');
  const cash = { amount: '90.00', currency: 'EUR', expected_outstanding: '90.00', paid_on: '2026-10-16', method: 'bank_transfer' };
  const paid = await owner.api('POST', `/issued/${issued.id}/payments`, cash, 200, { 'Idempotency-Key': 'synthetic-pilot-payment-command' });
  const paidReplay = await owner.api('POST', `/issued/${issued.id}/payments`, cash, 200, { 'Idempotency-Key': 'synthetic-pilot-payment-command' });
  if (paid.payment.id !== paidReplay.payment.id || paid.invoice.outstanding !== '0.00') throw new Error('Cash replay or balance failed');
  state.issued_id = issued.id;state.payment_id = paid.payment.id;state.cash = cash;
  report.actions.push('API issue, EUR10 credit and EUR90 payment replays preserved one result per command');
  const bytes = Buffer.from('Synthetic supporting evidence for controlled restore\n');
  const up = await owner.context.request.post(`${state.base}/api/v1/issued/${issued.id}/attachments`, { headers: { Authorization: `Bearer ${state.owner_token}`, 'Idempotency-Key': 'synthetic-pilot-attachment-command' }, multipart: { file: { name: 'synthetic-restore.txt', mimeType: 'text/plain', buffer: bytes } } });
  if (up.status() !== 201) throw new Error('Supporting source upload failed');
  state.attachment_id = (await up.json()).id;state.attachment_sha256 = createHash('sha256').update(bytes).digest('hex');
  const bank = Buffer.from(`date,description,credit,debit,currency\n2026-10-16,Synthetic payment ${issued.number},90.00,,EUR\n`);
  const imported = await owner.context.request.post(state.base + '/api/v1/reconciliation/import', { headers: { Authorization: `Bearer ${state.owner_token}` }, multipart: { file: { name: 'synthetic-bank.csv', mimeType: 'text/csv', buffer: bank } } });
  if (imported.status() !== 201) throw new Error('Bank import failed');
  state.statement_id = (await imported.json()).statement_id;
  await owner.page.goto(state.base + '/reconciliation');
  await owner.page.getByRole('button', { name: 'Reconcile', exact: true }).click();
  await owner.page.getByRole('button', { name: 'Find match', exact: true }).click();
  const candidate = owner.page.getByText(/Invoice payment ·/).locator('..');
  const matchedResponse = owner.page.waitForResponse(r => /\/reconciliation\/lines\/[^/]+\/match$/.test(r.url()));
  await candidate.getByRole('button', { name: 'Match', exact: true }).click();
  if ((await matchedResponse).status() !== 200) throw new Error('Browser match failed');
  const lines = await owner.api('GET', `/reconciliation/statements/${state.statement_id}/lines`);
  if (lines.length !== 1 || lines[0].currency !== 'EUR' || lines[0].matched_kind !== 'issued_payment' || lines[0].matched_id !== state.payment_id) throw new Error('Bank match did not persist against the exact ledger entry');
  state.bank_line_id = lines[0].id;
  report.actions.push('Browser matched real EUR90 bank line to the exact issued-payment ledger entry');

  await owner.page.goto(state.base + '/invoices');
  await owner.page.getByRole('button', { name: 'Accounting CSV', exact: true }).click();
  const section = owner.page.getByRole('region', { name: 'Accounting export preview' });
  await section.getByLabel('Legal entity').selectOption(state.entity_id);
  await section.getByLabel('From', { exact: true }).fill('2026-10-01');
  await section.getByLabel('To', { exact: true }).fill('2026-10-31');
  const previewResponse = owner.page.waitForResponse(r => r.url().endsWith('/export/accounting/preview'));
  await section.getByRole('button', { name: 'Preview approved invoices', exact: true }).click();
  const preview = await (await previewResponse).json();
  if (preview.included !== 1) throw new Error('Accountant preview population differs');
  state.export_token = preview.token;
  const downloadEvent = owner.page.waitForEvent('download');
  const downloadResponse = owner.page.waitForResponse(r => r.url().endsWith('/export/accounting/download'));
  await section.getByRole('button', { name: 'Download previewed CSV', exact: true }).click();
  const [download, response] = await Promise.all([downloadEvent, downloadResponse]);
  await download.saveAs(state.output + '/accounting.csv');
  const saved = readFileSync(state.output + '/accounting.csv');
  if (!saved.equals(await response.body())) throw new Error('Saved accountant bytes differ');
  const rows = JSON.parse(execFileSync('python3', ['-c', 'import csv,io,json,sys;print(json.dumps(list(csv.DictReader(io.StringIO(sys.stdin.read())))))'], { input: saved, encoding: 'utf8' }));
  if (rows.length !== 1 || rows[0].Net !== '100.00' || rows[0].VAT !== '21.00' || rows[0].Gross !== '121.00' || rows[0]['Gross EUR'] !== '121.00') throw new Error('Accountant money basis differs');
  state.csv_sha256 = createHash('sha256').update(saved).digest('hex');
  report.actions.push('Browser preview/download saved exact one-row EUR100 net / EUR21 VAT / EUR121 gross CSV');
  report.accounting = { rows: 1, net: '100.00', vat: '21.00', gross: '121.00', currency: 'EUR', bytes: saved.length, sha256: state.csv_sha256, exact_response_bytes: true };
  report.browser_errors = errors.length;
  if (errors.length) throw new Error('Browser runtime errors occurred');
  save('state.json', state);save('browser.json', report);console.log(JSON.stringify(report));
} finally {
  for (const context of contexts) await context.close();
  await browser.close();
}
