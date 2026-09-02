#!/usr/bin/env node
// Capture one phase: login → navigate → steps → assert → screenshot + video.
// Usage: node record.mjs <resolved.json>
// Exit 0 always writes phase_dir/{screenshot.png,video.webm,meta.json}.
// Errors print "AUTH_FAILED:..." / "STEP_FAILED:..." to stderr and exit non-zero.
import { readFileSync, writeFileSync, renameSync, unlinkSync } from 'node:fs';
import { createRequire } from 'node:module';
import { createHmac, createHash } from 'node:crypto';
import { join } from 'node:path';

const resolved = JSON.parse(readFileSync(process.argv[2], 'utf8'));
const { chromium } = createRequire(join(resolved.playwright_root, 'package.json'))('playwright');

const LOCAL = ['localhost', '127.0.0.1', '::1', '[::1]']; // WHATWG URL keeps the brackets, Python's urlparse strips them
const isLocal = (u) => { try { return LOCAL.includes(new URL(u).hostname); } catch { return false; } };
if (!isLocal(resolved.url)) {
  console.error(`NON_LOCAL_TARGET: refusing ${resolved.url}`); // defense in depth; validate.py already checks
  process.exit(9);
}
// A localhost URL can 3xx/JS-redirect to a remote host; the initial-URL check
// above does not catch that. assertLanded() re-validates the FINAL url after
// every navigation so an open-redirect can't smuggle remote content into a
// capture labeled localhost.
const assertLanded = () => {
  if (!isLocal(page.url())) throw new Error(`NON_LOCAL_TARGET: navigation landed on ${page.url()}`);
};

const vp = { width: resolved.viewport.width, height: resolved.viewport.height };
const dir = resolved.phase_dir;
const wantVideo = resolved.mode === 'video'; // still mode needs only the screenshot
const started = new Date().toISOString();
const browser = await chromium.launch({ headless: true });
const context = await browser.newContext({
  viewport: vp,
  ...(wantVideo ? { recordVideo: { dir, size: vp } } : {}),
});
const page = await context.newPage();

try {
  const auth = resolved.auth || { method: 'none' };
  if (auth.method === 'hmac-login-as') {
    const secret = process.env[auth.secret_env] || auth.secret_default;
    if (!secret) throw new Error(`AUTH_FAILED: no secret in $${auth.secret_env} and no secret_default`);
    const body = JSON.stringify({ email: auth.role_entry.email });
    const ts = Date.now().toString();
    const sig = createHmac('sha256', secret).update(`${ts}:${body}`).digest('hex');
    const resp = await context.request.post(resolved.base_url.replace(/\/$/, '') + auth.login_as_path, {
      headers: { 'Content-Type': 'application/json', 'X-Test-Signature': sig, 'X-Test-Timestamp': ts },
      data: body,
    });
    if (!resp.ok()) throw new Error(`AUTH_FAILED: login-as ${resp.status()} ${(await resp.text()).slice(0, 300)}`);
  } else if (auth.method === 'form') {
    if (auth.role_entry.password_plain) {
      throw new Error('AUTH_FAILED: role_entry.password_plain is refused — config.json must reference a password_env, never hold the secret');
    }
    const password = process.env[auth.role_entry.password_env || ''];
    if (!password) throw new Error(`AUTH_FAILED: no password in $${auth.role_entry.password_env}`);
    await page.goto(resolved.base_url.replace(/\/$/, '') + auth.login_path, { waitUntil: 'load' });
    await page.fill(auth.user_selector, auth.role_entry.email);
    await page.fill(auth.pass_selector, password);
    await page.click(auth.submit_selector);
    await page.waitForSelector(auth.post_login_selector, { timeout: 20000 })
      .catch(() => { throw new Error('AUTH_FAILED: post_login_selector never appeared'); });
  }

  await page.goto(resolved.url, { waitUntil: 'load' });
  assertLanded();
  await page.waitForLoadState('networkidle', { timeout: 8000 }).catch(() => {});
  await page.waitForTimeout(500);
  assertLanded(); // a delayed client-side redirect may fire during settle

  for (const [i, s] of resolved.steps.entries()) {
    try {
      if (s.action === 'goto') { await page.goto(resolved.base_url.replace(/\/$/, '') + s.path, { waitUntil: 'load' }); assertLanded(); }
      else if (s.action === 'click') await page.click(s.selector, { timeout: 10000 });
      else if (s.action === 'hover') await page.hover(s.selector, { timeout: 10000 });
      else if (s.action === 'fill') await page.fill(s.selector, s.value, { timeout: 10000 });
      else if (s.action === 'press') await page.keyboard.press(s.key);
      else if (s.action === 'select') await page.selectOption(s.selector, s.value, { timeout: 10000 });
      else if (s.action === 'waitFor') await page.waitForSelector(s.selector, { timeout: 10000 });
      else if (s.action === 'waitMs') await page.waitForTimeout(s.ms);
    } catch (e) {
      throw new Error(`STEP_FAILED: steps[${i}] (${s.action} ${s.selector || s.path || s.key || ''}): ${e.message.split('\n')[0]}`);
    }
  }

  if (process.env.POF_TEST_CRASH) process.exit(17); // selftest hook: die before meta exists

  await page.waitForTimeout(700); // let the final state render into the video
  assertLanded(); // a step (e.g. click) may have navigated off localhost

  let assertResult = { defined: false, passed: null, detail: null };
  if (resolved.assert) {
    const a = resolved.assert;
    let passed;
    if (a.type === 'text') {
      const text = await page.evaluate(() => document.body.innerText);
      passed = text.includes(a.text);
      assertResult = { defined: true, passed, detail: `text "${a.text}" ${passed ? 'found' : 'NOT found'} in page` };
    } else {
      const visible = await page.locator(a.selector).first().isVisible().catch(() => false);
      passed = a.type === 'visible' ? visible : !visible;
      assertResult = { defined: true, passed, detail: `${a.selector} is ${visible ? 'visible' : 'not visible'} (wanted ${a.type})` };
    }
  }

  await page.screenshot({ path: join(dir, 'screenshot.png') });
  let videoRel = null;
  if (wantVideo) {
    const video = page.video();
    await context.close(); // finalizes the webm
    renameSync(await video.path(), join(dir, 'video.webm'));
    videoRel = 'video.webm';
  } else {
    await context.close();
  }

  const stepsHash = createHash('sha256')
    .update(JSON.stringify({ url: resolved.url, steps: resolved.steps, viewport: vp, role: resolved.role }))
    .digest('hex');
  writeFileSync(join(dir, 'meta.json'), JSON.stringify({
    url: resolved.url, viewport: resolved.viewport, role: resolved.role, mode: resolved.mode,
    steps_hash: stepsHash, assert: assertResult, started, finished: new Date().toISOString(),
    screenshot: 'screenshot.png', video: videoRel,
  }, null, 2));
  await browser.close();
  console.log(`OK ${dir}`);
} catch (e) {
  // The success path screenshots only after the last step, so a STEP_FAILED —
  // the most common failure — would otherwise leave the caller with no image to
  // debug against. Never on a remote landing: keeping that frame is the thing
  // NON_LOCAL_TARGET exists to prevent.
  if (!e.message.startsWith('NON_LOCAL_TARGET')) {
    await page.screenshot({ path: join(dir, 'screenshot.png') }).catch(() => {});
  }
  const orphan = wantVideo ? page.video() : null;
  await context.close().catch(() => {});
  // The webm keeps Playwright's random name until the success path renames it,
  // so on failure it is unreferenced litter in a directory whose contents are
  // hash-listed in result.json.
  if (orphan) { try { unlinkSync(await orphan.path()); } catch { /* nothing to clean */ } }
  await browser.close().catch(() => {});
  console.error(e.message);
  process.exit(e.message.startsWith('AUTH_FAILED') ? 7 : e.message.startsWith('STEP_FAILED') ? 8 : 1);
}
