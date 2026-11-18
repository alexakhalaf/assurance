// End-to-end smoke test of the web UI against a running API with at least two mock snapshots.
//   node tools/e2e/smoke.cjs http://127.0.0.1:8000 admin <password>
// Fails (exit 1) on any page error, console error, failed API call, or page that does not render.
const { chromium } = require("playwright");

const [base = "http://127.0.0.1:8000", user = "admin", password = process.env.ASSURANCE_E2E_PASSWORD] = process.argv.slice(2);
const TABLES = ["facts", "hardware", "interfaces", "portchannels", "neighbors", "routes", "bgp", "ospf", "vrfs", "fhrp",
  "arp", "mac", "vlans", "stp", "ntp", "snmp", "aaa", "config"];
const PAGES = [
  ["/", "Dashboard"], ["/devices", "Devices"], ["/snapshots", "Snapshots"], ["/compare", "Changes"],
  ["/checks", "Intent checks"], ["/verify", null], ["/topology", "Topology"], ["/reports", null],
  ["/search?q=10.10.1.1", null], ["/discovery", null], ["/settings", "Settings"],
  ...TABLES.map((t) => [`/tables/${t}`, null]),
];

(async () => {
  const browser = await chromium.launch();
  const page = await browser.newPage({ viewport: { width: 1440, height: 900 } });
  const problems = [];
  let where = "login";
  page.on("pageerror", (e) => problems.push(`${where}: page error: ${e.message}`));
  page.on("console", (m) => { if (m.type() === "error") problems.push(`${where}: console: ${m.text()}`); });
  page.on("response", (r) => {
    if (r.url().includes("/api/") && r.status() >= 500) problems.push(`${where}: ${r.status()} ${r.url()}`);
  });
  const check = (ok, msg) => { if (!ok) problems.push(`${where}: ${msg}`); };

  await page.goto(base + "/");
  await page.fill("input >> nth=0", user);
  await page.fill("input[type=password]", password);
  await page.keyboard.press("Enter");
  await page.waitForSelector("main h1", { timeout: 15000 });

  for (const [path, title] of PAGES) {
    where = path;
    await page.goto(base + path);
    try {
      await page.waitForSelector("main h1", { timeout: 15000 });
      await page.waitForLoadState("networkidle", { timeout: 15000 });
    } catch { check(false, "did not render a page title"); continue; }
    if (title) check((await page.textContent("main h1"))?.trim() === title, `title is not "${title}"`);
  }

  where = "/tables/interfaces (grid)";
  await page.goto(base + "/tables/interfaces");
  await page.waitForLoadState("networkidle");
  check((await page.locator("main table tbody tr").count()) > 0, "interfaces grid shows no rows");

  where = "/compare (changes)";
  await page.goto(base + "/compare");
  await page.waitForLoadState("networkidle");
  check(/[1-9]\d* change\(s\)/.test(await page.textContent("main")), "no changes between the two mock snapshots");

  where = "/devices/yyc-core-rtr01";
  await page.goto(base + "/devices/yyc-core-rtr01");
  await page.waitForSelector("main h1");
  check((await page.textContent("main h1"))?.includes("yyc-core-rtr01"), "device page title");

  where = "theme toggle";
  const before = await page.evaluate(() => document.documentElement.dataset.theme);
  await page.click("button[aria-label='Toggle dark mode']");
  check(before !== (await page.evaluate(() => document.documentElement.dataset.theme)), "theme did not change");

  await browser.close();
  if (problems.length) {
    console.error(`${problems.length} problem(s):\n` + problems.join("\n"));
    process.exit(1);
  }
  console.log(`ok: ${PAGES.length + 3} pages and interactions checked`);
})().catch((e) => { console.error(e); process.exit(1); });
