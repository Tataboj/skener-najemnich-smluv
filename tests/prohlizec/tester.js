// Automaticky tester dema (Playwright): node tests/prohlizec/tester.js index.html
const { chromium } = require("playwright");
const path = require("path");
const FILE = "file://" + path.resolve(process.argv[2]);
let fails = 0, checks = 0;
const ok = (c, msg) => { checks++; if (!c) { fails++; console.log("FAIL:", msg); } };

(async () => {
  const browser = await chromium.launch();
  const ctx = await browser.newContext({ viewport: { width: 1280, height: 900 } });
  const page = await ctx.newPage();
  const errors = [];
  page.on("console", (m) => { if (m.type() === "error" && !/fonts\.g/.test((m.location() || {}).url || "") && !/ERR_TUNNEL/.test(m.text())) errors.push(m.text()); });
  page.on("pageerror", (e) => errors.push("pageerror " + e.message));
  await page.goto(FILE);
  await page.waitForSelector("tr.row");
  const total = await page.evaluate(() => window.__skener.N.length);
  const rowsN = () => page.$$eval("#rows tr.row", (r) => r.length);
  const shown = () => page.$eval("#shown", (e) => +e.textContent);
  const reset = () => page.evaluate(() => window.__skener.resetAll(true));
  ok((await rowsN()) === total && (await shown()) === total, `vychozi pocet ${await rowsN()} != ${total}`);

  // 1) chipy stavu a sluzeb: cislo na tlacitku = radky; opetovny klik zrusi
  for (const sel of ["#stavy [data-stav]", "#sluzby [data-sluzba]"]) {
    const n = await page.$$eval(sel, (b) => b.length);
    for (let i = 0; i < n; i++) {
      const b = (await page.$$(sel))[i];
      if (await b.isDisabled()) continue;
      const num = +(await b.$eval(".n", (e) => e.textContent));
      const label = await b.textContent();
      await b.click();
      ok((await rowsN()) === num && (await shown()) === num, `${label.trim()}: tlacitko ${num}, radku ${await rowsN()}`);
      ok((await (await page.$$(sel))[i].getAttribute("aria-pressed")) === "true", `${label} neni aria-pressed`);
      await (await page.$$(sel))[i].click();
      ok((await rowsN()) === total, `${label} druhy klik nezrusil filtr`);
    }
  }
  // 2) dlazdice Prahy + mesta: cislo = radky
  const tiles = await page.$$eval("#carto .tile", (t) => t.map((x) => [x.dataset.lok, x.disabled, x.querySelector(".cnt").textContent]));
  for (const [lok, dis, cnt] of tiles) {
    if (dis) { ok(cnt === "·", `${lok} zakazana dlazdice ma cislo ${cnt}`); continue; }
    await page.click(`#carto .tile[data-lok="${lok}"]`);
    ok((await rowsN()) === +cnt, `${lok}: dlazdice ${cnt}, radku ${await rowsN()}`);
    const lokRows = await page.$$eval("#rows tr.row td[data-l=Místo]", (t) => [...new Set(t.map((x) => x.textContent))]);
    ok(lokRows.length === 1 && lokRows[0] === lok, `${lok}: radky z jinych mist ${lokRows}`);
    await page.click(`#carto .tile[data-lok="${lok}"]`);
  }
  const mesta = await page.$$eval("#mesta [data-lok]", (t) => t.map((x) => [x.dataset.lok, x.querySelector(".n").textContent]));
  for (const [lok, cnt] of mesta) {
    await page.click(`#mesta [data-lok="${lok}"]`);
    ok((await rowsN()) === +cnt, `${lok}: chip ${cnt}, radku ${await rowsN()}`);
    await page.click(`#mesta [data-lok="${lok}"]`);
  }
  ok((await rowsN()) === total, "po mestech neni vse");
  const sumTiles = tiles.reduce((a, t) => a + (t[1] ? 0 : +t[2]), 0) + mesta.reduce((a, m) => a + +m[1], 0);
  ok(sumTiles === total, `soucet dlazdic+mest ${sumTiles} != ${total}`);

  // 3) kombinace: stav + lokalita, prilezitosti; pocty prepocitane
  await page.click('#stavy [data-stav="neurcita"]');
  const t2 = await page.$$eval("#carto .tile:not(:disabled), #mesta [data-lok]", (t) => t.map((x) => [x.dataset.lok, (x.querySelector(".cnt") || x.querySelector(".n")).textContent]));
  const sumT2 = t2.reduce((a, x) => a + (+x[1] || 0), 0);
  ok(sumT2 === (await rowsN()), `neurcita: soucet mist ${sumT2} != radku ${await rowsN()}`);
  if (t2.length) {
    const [lok, cnt] = t2[0];
    await page.click(`[data-lok="${lok}"]`);
    ok((await rowsN()) === +cnt, `kombinace neurcita+${lok}: ${cnt} vs ${await rowsN()}`);
    const chipN = await page.$eval('#stavy [data-stav="neurcita"] .n', (e) => +e.textContent);
    ok(chipN === (await rowsN()), `chip neurcita po vyberu mista ${chipN} != ${await rowsN()}`);
    ok((await page.$$eval("#activebar .fchip", (x) => x.length)) === 2, "lista nema 2 filtry");
    await page.click("#clearall");
    ok((await rowsN()) === total, "Zrusit vse nefunguje");
  }
  await page.check("#jenprilez");
  const pr = await rowsN();
  const prExp = await page.evaluate(() => window.__skener.N.filter((n) => ["relokace", "prejednani", "kontrakce", "expanze"].includes(n.sluzba)).length);
  ok(pr === prExp, `jen prilezitosti ${pr} != ${prExp}`);
  const stSum = await page.$$eval("#stavy .n", (x) => x.reduce((a, e) => a + +e.textContent, 0));
  ok(stSum === pr, `prilezitosti: soucet stavu ${stSum} != ${pr}`);
  await page.click('#activebar [data-clear="prilez"]');
  ok((await rowsN()) === total && !(await page.isChecked("#jenprilez")), "zruseni prilezitosti pres listu");

  // 4) KPI
  const kpis = await page.$$eval(".kpi", (k) => k.map((x) => x.querySelector(".v").textContent));
  for (let i = 0; i < 3; i++) {
    await page.click(`.kpi[data-kpi="${i}"]`);
    ok((await rowsN()) === +kpis[i], `KPI ${i}: ${kpis[i]} vs radku ${await rowsN()}`);
    ok((await page.getAttribute(`.kpi[data-kpi="${i}"]`, "aria-pressed")) === "true", `KPI ${i} neni oznacene`);
  }
  await reset();

  // 5) naseptavac: kazda polozka -> pocet = radky
  for (const q of ["praha 8", "p10", "kodan", "ministerstvo", "urad prace", "72080043", "1rif", "brno", "financni", "xyzzy"]) {
    await page.fill("#q", q);
    await page.waitForTimeout(30);
    const opts = await page.$$eval("#sugg [role=option]", (o) => o.map((x) => [x.dataset.i, x.querySelector(":scope > .c").textContent, x.textContent]));
    ok(opts.length >= 1, `naseptavac ${q}: zadne polozky`);
    for (const [i, c, txt] of opts) {
      await page.fill("#q", q); await page.waitForTimeout(20);
      await page.dispatchEvent(`#opt${i}`, "mousedown");
      const n = parseInt(c);
      ok((await rowsN()) === n || (n === 0 && (await page.$("#clear2"))), `naseptavac "${q}" -> "${txt.trim()}": ${n} vs ${await rowsN()}`);
      await reset();
    }
  }
  // klavesnice: sipka + Enter
  await page.fill("#q", "praha"); await page.press("#q", "ArrowDown"); await page.press("#q", "Enter");
  ok((await page.$$eval("#activebar .fchip", (x) => x.length)) === 1, "klavesnice: filtr nepridan");
  await reset();

  // 6) razeni
  for (const k of ["najemce", "lokalita", "stav", "datum", "kc", "sluzba"]) {
    await page.click(`th [data-sort="${k}"]`);
    await page.click(`th [data-sort="${k}"]`);
    ok((await rowsN()) === total, `razeni ${k} zmenilo pocet`);
  }
  if (await page.$("#sortreset")) await page.click("#sortreset");

  // 7) detail kazdeho najmu: odkazy, zadne undefined/NaN/null, Esc zavre
  const ids = await page.evaluate(() => window.__skener.N.map((n) => n.id));
  const RX = [/^https:\/\/smlouvy\.gov\.cz\/smlouva\/\d+$/, /^https:\/\/smlouvy\.gov\.cz\/smlouva\/soubor\/\d+\/.+/, /^https:\/\/ares\.gov\.cz\/ekonomicke-subjekty\?ico=\d{8}$/,
    /^https:\/\/or\.justice\.cz\/ias\/ui\/rejstrik-\$firma\?ico=\d{8}$/, /^https:\/\/www\.rzp\.cz\/verejne-udaje\/cs\/udaje\/vyber-subjektu;ico=\d{8};$/,
    /^https:\/\/smlouvy\.gov\.cz\/vyhledavani\?subject_idnum=\d{8}$/, /^https:\/\/www\.hlidacstatu\.cz\/subjekt\/\d{8}$/, /^https:\/\/www\.google\.com\/maps\/search\/\?api=1&query=.+/];
  const badLinks = new Set();
  for (const id of ids) {
    await page.click(`tr.row[data-id="${id}"]`);
    await page.waitForSelector("#drawer:not([hidden])");
    const txt = await page.$eval("#drawer", (d) => d.innerText);
    ok(!/undefined|NaN|\bnull\b|\[object/.test(txt), `${id}: text obsahuje undefined/NaN/null`);
    const hrefs = await page.$$eval("#drawer a[href]", (a) => a.map((x) => x.href));
    for (const h of hrefs) if (!RX.some((r) => r.test(h.replace(/#page=\d+$/, "")))) badLinks.add(id + " " + h);
    for (const h of hrefs.filter((x) => x.includes("google.com/maps"))) {
      const q = decodeURIComponent(h.split("query=")[1] || "");
      const lok = await page.evaluate((i) => window.__skener.N.find((n) => n.id === i).lokalita || "", id);
      ok(!/^(Praha|Praha \d+|Praha – obvod neuveden)$/.test(q.trim()) && q.trim() !== lok, `${id}: mapa jen na mesto "${q}"`);
      ok(/\d/.test(q) || /přibližně|z předmětu/.test(txt), `${id}: mapa bez cisla a bez upozorneni "${q}"`);
    }
    ok(hrefs.every((h) => h.startsWith("https://")), `${id}: odkaz neni https`);
    ok(await page.$eval(`tr.row[data-id="${id}"]`, (r) => r.getAttribute("aria-selected") === "true"), `${id}: radek neni oznacen`);
    await page.keyboard.press("Escape");
    ok(await page.$eval("#drawer", (d) => d.hidden), `${id}: Esc nezavrel`);
  }
  ok(badLinks.size === 0, "spatne odkazy: " + [...badLinks].slice(0, 5).join(" | "));
  // tlacitko "Vsechny najmy pronajimatele" v detailu
  await page.click(`tr.row[data-id="${ids[0]}"]`);
  const fp = await page.$("#drawer [data-fp]");
  if (fp) { await fp.click(); ok((await page.$$eval("#activebar .fchip", (x) => x.length)) === 1 && (await rowsN()) >= 1, "filtr pronajimatele z detailu"); }
  await reset();
  // ukazka v hlavicce
  await page.click("#bestbtn"); ok(!(await page.$eval("#drawer", (d) => d.hidden)), "ukazka neotevrela detail");
  await page.click("#dclose"); ok(await page.$eval("#drawer", (d) => d.hidden), "krizek nezavrel");

  // 8) kalendar: pocet = kalendarove polozky, klik otevre detail
  const calN = await page.$$eval("#cal .ev", (e) => e.length);
  ok(calN === +(await page.$eval("#caltotal", (e) => e.textContent)), "kalendar: soucet nesedi");
  if (calN) { await page.click("#cal .ev"); ok(!(await page.$eval("#drawer", (d) => d.hidden)), "kalendar neotevrel"); await page.keyboard.press("Escape"); }
  await page.click('#stavy [data-stav="pozdeji"]');
  const calF = await page.$$eval("#cal .ev", (e) => e.map((x) => x.className));
  ok(calF.every((c) => c.includes("s-pozdeji")), "kalendar nerespektuje filtr");
  await reset();
  if (await page.$("#calneur")) { await page.click("#calneur"); ok((await page.$eval('#stavy [data-stav="neurcita"]', (b) => b.getAttribute("aria-pressed"))) === "true", "calneur"); await reset(); }

  // 9) benchmark: kazdy bod otevre detail
  const pts = await page.$$eval("#bench .pt", (p) => p.length);
  ok(pts === (await page.evaluate(() => window.__skener.N.filter((n) => n.kc_m2.ok).length)), "benchmark: pocet bodu");
  await page.click("#bench .pt >> nth=0", { force: true }); ok(!(await page.$eval("#drawer", (d) => d.hidden)), "bod neotevrel detail"); await page.keyboard.press("Escape");

  // 10) dotazy
  const qn = await page.$$eval(".qbtn", (q) => q.length);
  for (let i = 0; i < qn; i++) {
    await page.click(`.qbtn[data-q="${i}"]`);
    const li = await page.$$eval("#ans li", (l) => l.length);
    ok(li > 0, `dotaz ${i}: prazdna odpoved`);
    ok(!/undefined|NaN/.test(await page.$eval("#ans", (a) => a.innerText)), `dotaz ${i}: undefined`);
    const b = await page.$("#ans [data-id], #ans [data-fp]");
    if (b) { await b.click(); await page.keyboard.press("Escape"); await reset(); }
  }
  // 11) kopirovani
  await page.click("#copy");
  await page.waitForTimeout(150);
  ok(!(await page.$eval("#toast", (t) => t.hidden)) || !(await page.$eval("#drawer", (d) => d.hidden)), "kopirovani bez reakce");
  await page.keyboard.press("Escape");
  ok(!(await page.$eval("#csv", (b) => b.hidden)), "CSV tlacitko v lokalni verzi chybi");
  // hash
  const p2 = await ctx.newPage(); await p2.goto(FILE + "#" + ids[3]); await p2.waitForSelector("#drawer:not([hidden])"); ok(true, "hash"); await p2.close();

  // 12) sirky a temata: zadny vodorovny posuvnik (i s otevrenym detailem)
  const bodyTxt = await page.$eval("body", (b) => b.innerText);
  ok(!/undefined|NaN|\[object/.test(bodyTxt), "stranka obsahuje undefined/NaN");
  for (const scheme of ["light", "dark"]) {
    await page.emulateMedia({ colorScheme: scheme });
    for (const w of [1280, 1024, 768, 400, 360]) {
      await page.setViewportSize({ width: w, height: 900 });
      await page.waitForTimeout(50);
      const ov = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
      ok(ov <= 0, `${scheme} ${w}px: vodorovny posuvnik ${ov}px`);
      await page.click(`tr.row[data-id="${ids[0]}"]`);
      const ov2 = await page.evaluate(() => { const d = document.getElementById("drawer"); return d.scrollWidth - d.clientWidth; });
      ok(ov2 <= 0, `${scheme} ${w}px: detail siroky o ${ov2}px`);
      await page.keyboard.press("Escape");
    }
  }
  ok(errors.length === 0, "chyby v konzoli: " + errors.join(" | "));
  console.log(`\nKONTROL: ${checks}, CHYB: ${fails}`);
  await browser.close();
  process.exit(fails ? 1 : 0);
})();
