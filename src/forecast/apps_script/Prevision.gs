/**
 * Prévision de production : script du Google Sheet (Extensions > Apps Script).
 *
 * Quand quelqu'un modifie, dans un onglet de pôle, un mois, une proba, un KV, un coût, une date de
 * début ou de fin, une part maintenance ou un commentaire :
 *   1. la valeur saisie est enregistrée dans l'onglet caché _saisies (une ligne par Réf.) ;
 *   2. la ligne est recalculée tout de suite : le reste à produire moins les mois saisis se répartit
 *      sur les autres mois ouverts, selon la forme par défaut (règles du dashboard commercial).
 * Le serveur refait chaque nuit exactement le même calcul (src/forecast/engine.py et model.py) ;
 * les fonctions distribute, defaultWeights et computeRow ci-dessous en sont la traduction ligne à
 * ligne, vérifiée par tests/test_forecast_js_parity.py.
 *
 * Vider une cellule = revenir au calcul automatique.
 *
 * Le menu « Prévision » n'est qu'un secours, le recalcul se fait déjà à chaque modification :
 *   - « Recalculer la ligne » : relit la ligne sélectionnée, enregistre ce qui a été tapé sans être pris
 *     en compte (par exemple quand deux personnes modifient en même temps), puis la recalcule ;
 *   - « Effacer toutes les saisies orange des mois de la ligne » : vide d'un coup tous les mois tapés
 *     de la ligne ; son reste à produire se répartit à nouveau selon les règles.
 */

var POLES = ['Travaux', 'Conception', 'Maintenance contrats', 'Maintenance TS'];  // the tabs of rows
var ROW_FIELDS = 3;
var ROW_FIRST = 9;
var EPS = 0.005;
var COLORS = { typed: '#f9cb9c', input: '#e8f2ff', calc: '#ffffff' };  // the server's colours (_calcul) win

// ------------------------------------------------------------------ months ("AAAA-MM")

function monthIndex(k) { return parseInt(k.slice(0, 4), 10) * 12 + parseInt(k.slice(5, 7), 10) - 1; }
function monthKey(i) { var y = Math.floor(i / 12), m = (i % 12) + 1; return y + '-' + (m < 10 ? '0' : '') + m; }
function monthAdd(k, n) { return monthKey(monthIndex(k) + n); }
function isMonthKey(v) { return typeof v === 'string' && /^\d{4}-(0[1-9]|1[0-2])$/.test(v); }
function monthSpan(a, b) { var out = []; for (var i = monthIndex(a); i <= monthIndex(b); i++) out.push(monthKey(i)); return out; }

function num(v) {
  if (v === null || v === undefined || v === '') return null;
  if (typeof v === 'number') return v;
  var t = String(v).replace(/[\s €]/g, '').replace(',', '.');
  var x = parseFloat(t);
  return isNaN(x) ? null : x;
}

// ------------------------------------------------------------------ engine (twin of engine.py)

function distribute(remaining, weights, pins, grid, openFrom, fallbackMonths) {
  var gridSet = {}, i;
  for (i = 0; i < grid.length; i++) gridSet[grid[i]] = true;
  var lastIndex = monthIndex(grid[grid.length - 1]);
  var openIndex = monthIndex(openFrom);
  var typed = {}, typedSum = 0;
  Object.keys(pins).forEach(function (m) {
    if (gridSet[m] && pins[m] !== null && pins[m] !== undefined) { typed[m] = Number(pins[m]); typedSum += typed[m]; }
  });
  var values = {};
  grid.forEach(function (m) { values[m] = typed.hasOwnProperty(m) ? typed[m] : 0; });
  var result = { values: values, beyond: 0, unplaced: 0, fallback: false };
  var toPlace = Number(remaining || 0) - typedSum;
  if (toPlace < -EPS) { result.unplaced = toPlace; return result; }
  if (toPlace <= EPS) return result;
  var shares = {}, total = 0;
  Object.keys(weights).forEach(function (m) {
    var w = Number(weights[m]);
    if (!w || w <= 0) return;
    var idx = monthIndex(m);
    if (idx < openIndex || typed.hasOwnProperty(m)) return;
    if (gridSet[m] || idx > lastIndex) shares[m] = (shares[m] || 0) + w;
  });
  Object.keys(shares).forEach(function (m) { total += shares[m]; });
  if (total <= 0) {
    var free = grid.filter(function (m) { return monthIndex(m) >= openIndex && !typed.hasOwnProperty(m); });
    var chosen = free.slice(0, Math.max(1, parseInt(fallbackMonths, 10) || 1));
    if (!chosen.length) { result.unplaced = toPlace; return result; }
    shares = {}; chosen.forEach(function (m) { shares[m] = 1; });
    total = chosen.length;
    result.fallback = true;
  }
  Object.keys(shares).sort().forEach(function (m) {
    var part = toPlace * shares[m] / total;
    if (gridSet[m]) values[m] += part; else result.beyond += part;
  });
  return result;
}

function defaultWeights(alloc, pending, openFrom, startOverride, stopOverride, pendingFrom) {
  var keys = alloc ? Object.keys(alloc).filter(function (k) { return alloc[k] > 0; }).sort() : [];
  var shifted = {};
  keys.forEach(function (k) { shifted[k] = alloc[k]; });
  if (!keys.length && !startOverride && !stopOverride) return {};
  if (keys.length) {
    var first = keys[0], target = null;
    if (startOverride) target = startOverride;
    else if (pending && monthIndex(first) < monthIndex(pendingFrom || openFrom)) target = pendingFrom || openFrom;
    if (target) {
      var shift = monthIndex(target) - monthIndex(first);
      shifted = {};
      keys.forEach(function (k) { shifted[monthAdd(k, shift)] = alloc[k]; });
      keys = Object.keys(shifted).sort();
    }
  }
  if (stopOverride) {
    var begin = startOverride ? startOverride
      : (keys.length ? (monthIndex(keys[0]) > monthIndex(openFrom) ? keys[0] : openFrom) : openFrom);
    var end = monthIndex(stopOverride) >= monthIndex(begin) ? stopOverride : begin;
    var out = {};
    monthSpan(begin, end).forEach(function (m) { out[m] = 1; });
    return out;
  }
  if (!keys.length) { var one = {}; one[startOverride] = 1; return one; }
  return shifted;
}

function computeRow(calc, typed, meta) {
  var grid = meta.grid, openFrom = meta.open_from, fallback = meta.fallback || 2;
  var manual = calc.kind === 'Manuel', alerts = [];
  var category = (manual && (typed.cat === 'Signé' || typed.cat === 'Pipe')) ? typed.cat : calc.cat;
  var ca = (manual && num(typed.ca) !== null) ? num(typed.ca) : Number(calc.ca || 0);
  var mai = num(typed.mai) !== null ? num(typed.mai) : Number(calc.mai || 0);
  var signed = category === 'Signé';
  var proba;
  if (signed) proba = 1;
  else if (num(typed.proba) !== null) proba = Math.max(0, Math.min(1, num(typed.proba)));
  else if (calc.proba === null || calc.proba === undefined || Number(calc.proba) <= 0) proba = 0.5;
  else proba = Math.max(0, Math.min(1, Number(calc.proba)));
  var startO = isMonthKey(typed.start) ? typed.start : null;
  var stopO = isMonthKey(typed.stop) ? typed.stop : null;
  var pins = {};
  Object.keys(typed).forEach(function (k) { if (isMonthKey(k) && num(typed[k]) !== null) pins[k] = num(typed[k]); });

  var weights, remaining, openIndex = monthIndex(openFrom), given = {};
  if (calc.fixed) {  // contracts and TS: their months are given; dates cannot be typed on them
    weights = {}; Object.keys(calc.fixed).forEach(function (m) { if (calc.fixed[m] > 0) weights[m] = calc.fixed[m]; });
    // Past grid months not closed yet show the contract's own value (nothing can be re-spread there).
    grid.forEach(function (m) {
      if (monthIndex(m) < openIndex && calc.fixed.hasOwnProperty(m) && !pins.hasOwnProperty(m)) given[m] = calc.fixed[m];
    });
    remaining = 0;
    Object.keys(calc.fixed).forEach(function (m) { if (monthIndex(m) >= monthIndex(grid[0])) remaining += calc.fixed[m]; });
  } else {
    weights = defaultWeights(calc.alloc, calc.kind === 'Devis', openFrom, startO, stopO, meta.pending_from);
    if (calc.closed) {  // closed by the finance: only the months typed on it count
      remaining = 0; grid.forEach(function (m) { if (pins.hasOwnProperty(m)) remaining += pins[m]; });
    }
    else if (calc.kind === 'Projet') {
      remaining = ca - mai - Number(calc.produced || 0);
      if (remaining < -EPS) { if (remaining <= -1) alerts.push('Réalisé supérieur au CA de ' + Math.round(-remaining) + ' €'); remaining = 0; }
    } else if (calc.kind === 'Devis') remaining = ca - mai;
    else if (manual && num(typed.ca) === null) { remaining = 0; Object.keys(pins).forEach(function (m) { remaining += pins[m]; }); }
    else remaining = ca - mai;
  }
  if (Math.abs(remaining) < EPS) remaining = 0;
  var allPins = {};
  Object.keys(given).forEach(function (m) { allPins[m] = given[m]; });
  Object.keys(pins).forEach(function (m) { allPins[m] = pins[m]; });
  var dist = distribute(remaining, weights, allPins, grid, openFrom, fallback);
  if (dist.fallback) alerts.push('Dates dépassées : reste réparti sur ' + fallback + ' mois');
  if (dist.unplaced <= -1) alerts.push('Saisies supérieures au reste de ' + Math.round(-dist.unplaced) + ' €');
  else if (dist.unplaced >= 1) alerts.push(Math.round(dist.unplaced) + ' € sans mois ouvert disponible');

  var kvDefault = Number(calc.kv_default) > 0 ? Number(calc.kv_default) : 1;
  var costO = num(typed.cost), kvO = num(typed.kv), base = Math.max(ca - mai, 0), kv, cost;
  if (costO !== null && costO > 0) { kv = base > 0 ? base / costO : kvDefault; cost = costO; }
  else { kv = (kvO !== null && kvO > 0) ? kvO : kvDefault; cost = base / kv; }
  var placed = 0; Object.keys(dist.values).forEach(function (m) { placed += dist.values[m]; });
  var gap = remaining - placed - dist.beyond;
  return {
    category: category, ca: ca, proba: proba, mai: mai, remaining: remaining, kv: kv, cost: cost,
    months: dist.values, beyond: dist.beyond, gap: Math.abs(gap) > EPS ? gap : 0, alerts: alerts,
    start: startO || calc.start, stop: stopO || calc.stop
  };
}

// ------------------------------------------------------------------ sheet side (Apps Script only)

function onOpen() {
  SpreadsheetApp.getUi().createMenu('Prévision')
    .addItem('Recalculer la ligne (si elle ne s\'est pas mise à jour)', 'recalcSelected')
    .addItem('Effacer toutes les saisies orange des mois de la ligne', 'releaseSelected')
    .addToUi();
}

function readJsonTab_(ss, name) {
  var sh = ss.getSheetByName(name), out = {}, rows = {};
  if (!sh || sh.getLastRow() < 1) return { sheet: sh, data: out, rows: rows };
  var values = sh.getRange(1, 1, sh.getLastRow(), 2).getValues();
  for (var i = 0; i < values.length; i++) {
    var ref = String(values[i][0] || '');
    if (!ref || ref === 'Réf.') continue;
    try { out[ref] = JSON.parse(values[i][1]); rows[ref] = i + 1; } catch (err) { /* ignore a damaged line */ }
  }
  return { sheet: sh, data: out, rows: rows };
}

function writeJson_(tab, ref, obj) {
  var text = JSON.stringify(obj);
  if (tab.rows[ref]) tab.sheet.getRange(tab.rows[ref], 2).setValue(text);
  else { tab.sheet.appendRow([ref, text]); tab.rows[ref] = tab.sheet.getLastRow(); }
}

function serialToMonth_(v) {
  if (v instanceof Date) return v.getFullYear() + '-' + ('0' + (v.getMonth() + 1)).slice(-2);
  if (typeof v === 'number') { var d = new Date(Date.UTC(1899, 11, 30) + v * 86400000); return d.getUTCFullYear() + '-' + ('0' + (d.getUTCMonth() + 1)).slice(-2); }
  var t = String(v || '').trim();
  if (isMonthKey(t)) return t;
  var p = t.replace(/-/g, '/').split('/').filter(String);
  if (p.length === 2) return p[1] + '-' + ('0' + p[0]).slice(-2);
  if (p.length === 3) return (p[2].length === 4 ? p[2] : p[0]) + '-' + ('0' + p[1]).slice(-2);
  return null;
}

function normalize_(field, value) {
  if (value === '' || value === null || value === undefined) return null;
  if (field === 'start' || field === 'stop') return serialToMonth_(value);
  if (field === 'cat' || field === 'label' || field === 'comment') return String(value).trim();
  var x = num(value);
  if (field === 'proba' && x !== null && x > 1) x = x / 100;
  return x;
}

function onEdit(e) {
  if (!e || !e.range) return;
  var sheet = e.range.getSheet();
  if (POLES.indexOf(sheet.getName()) < 0 || e.range.getLastRow() < ROW_FIRST) return;
  var lock = LockService.getDocumentLock();
  if (!lock.tryLock(20000)) return;
  try {
    var ss = sheet.getParent();
    var lastCol = sheet.getLastColumn();
    var fields = sheet.getRange(ROW_FIELDS, 1, 1, lastCol).getValues()[0].map(String);
    var refCol = fields.indexOf('ref');
    var inputs = readJsonTab_(ss, '_saisies'), rendu = readJsonTab_(ss, '_rendu'), calcTab = readJsonTab_(ss, '_calcul');
    var meta = calcTab.data.__meta;
    if (!meta) return;
    var touched = {};
    var r0 = Math.max(e.range.getRow(), ROW_FIRST), r1 = e.range.getLastRow();
    var c0 = e.range.getColumn(), c1 = e.range.getLastColumn();
    var block = sheet.getRange(r0, 1, r1 - r0 + 1, lastCol).getValues();
    for (var r = r0; r <= r1; r++) {
      var line = block[r - r0], ref = String(line[refCol] || '');
      if (!ref || !rendu.data[ref]) continue;  // new line: the server turns it into a manual line
      var editable = rendu.data[ref];
      var typed = inputs.data[ref] || {};
      for (var c = c0; c <= c1; c++) {
        var f = fields[c - 1];
        if (!f || !editable.hasOwnProperty(f) || (f === 'label' && ref.charAt(0) !== 'M')) continue;
        var v = normalize_(f, line[c - 1]);
        if (v === null || v === '') delete typed[f]; else typed[f] = v;
      }
      inputs.data[ref] = typed;
      writeJson_(inputs, ref, typed);
      touched[ref] = r;
    }
    Object.keys(touched).forEach(function (ref) { redrawRow_(sheet, fields, touched[ref], ref, inputs.data[ref] || {}, calcTab.data[ref], meta, rendu); });
  } finally {
    lock.releaseLock();
  }
}

function redrawRow_(sheet, fields, row, ref, typed, calc, meta, rendu) {
  if (!calc) return;
  var res = computeRow(calc, typed, meta);
  var colors = (meta && meta.colors) || COLORS;  // written by the server each night: no new paste when colours change
  var range = sheet.getRange(row, 1, 1, fields.length);
  var values = range.getValues()[0], backgrounds = range.getBackgrounds()[0];
  var editable = rendu.data[ref] || {};
  var serial = function (k) { if (!k) return ''; var y = +k.slice(0, 4), m = +k.slice(5, 7); return (Date.UTC(y, m - 1, 1) - Date.UTC(1899, 11, 30)) / 86400000; };
  var shown = {
    cat: res.category, ca: res.ca, proba: res.category === 'Pipe' ? res.proba : '', remaining: res.remaining,
    kv: res.kv, cost: res.cost, beyond: res.beyond || '', gap: res.gap || '', alerts: res.alerts.join(' · '),
    start: serial(res.start), stop: serial(res.stop)
  };
  if (sheet.getName() === 'Travaux') shown.mai = res.mai;
  meta.grid.forEach(function (m) { shown[m] = res.months[m] || 0; });
  var newRendu = {};
  for (var i = 0; i < fields.length; i++) {
    var f = fields[i];
    if (shown.hasOwnProperty(f) && f !== 'cost_next' && f !== 'margin_next') values[i] = shown[f];
    if (editable.hasOwnProperty(f)) {
      backgrounds[i] = typed.hasOwnProperty(f) ? colors.typed : colors.input;
      newRendu[f] = (f === 'start' || f === 'stop') ? (f === 'start' ? res.start : res.stop) || null : values[i];
    }
  }
  newRendu.label = editable.label;
  // Write the row in blocks that skip every formula cell (year and quarter totals, cost and margin),
  // so those formulas stay untouched.
  var formulas = range.getFormulas()[0], start = -1;
  for (var k = 0; k <= fields.length; k++) {
    var isFormula = k < fields.length && formulas[k] !== '';
    if (k < fields.length && !isFormula) { if (start < 0) start = k; continue; }
    if (start >= 0) {
      sheet.getRange(row, start + 1, 1, k - start).setValues([values.slice(start, k)]);
      start = -1;
    }
  }
  range.setBackgrounds([backgrounds]);
  rendu.data[ref] = newRendu;
  writeJson_(rendu, ref, newRendu);
}

function selectedRef_() {
  var sheet = SpreadsheetApp.getActiveSheet();
  if (POLES.indexOf(sheet.getName()) < 0) return null;
  var row = sheet.getActiveRange().getRow();
  if (row < ROW_FIRST) return null;
  var fields = sheet.getRange(ROW_FIELDS, 1, 1, sheet.getLastColumn()).getValues()[0].map(String);
  return { sheet: sheet, row: row, fields: fields, ref: String(sheet.getRange(row, fields.indexOf('ref') + 1).getValue() || '') };
}

function sameValue_(a, b) {
  var empty = function (x) { return x === null || x === undefined || x === ''; };
  if (empty(a) && empty(b)) return true;
  if (typeof a === 'number' && typeof b === 'number') return Math.abs(a - b) <= Math.max(0.005, 1e-9 * Math.abs(b));
  return String(a).trim() === String(b).trim();
}

// Same rule as the nightly refresh (build.detect_typed): a cell that differs from what was last drawn
// (_rendu) was typed by hand; an emptied one goes back to automatic. Catches an edit that onEdit missed.
function recordRow_(fields, line, ref, typed, before) {
  Object.keys(before || {}).forEach(function (f) {
    if (f === 'label' && ref.charAt(0) !== 'M') return;
    var i = fields.indexOf(f); if (i < 0) return;
    var now = line[i];
    if ((f === 'start' || f === 'stop') && now !== '' && now !== null) now = serialToMonth_(now);
    if (sameValue_(now, before[f])) return;
    var v = normalize_(f, line[i]);
    if (v === null || v === '') delete typed[f]; else typed[f] = v;
  });
  return typed;
}

function recalcSelected() {
  var s = selectedRef_(); if (!s || !s.ref) return;
  var lock = LockService.getDocumentLock();
  if (!lock.tryLock(20000)) { SpreadsheetApp.getUi().alert('Le fichier est occupé, réessayez dans un instant.'); return; }
  try {
    var ss = s.sheet.getParent();
    var inputs = readJsonTab_(ss, '_saisies'), rendu = readJsonTab_(ss, '_rendu'), calcTab = readJsonTab_(ss, '_calcul');
    var line = s.sheet.getRange(s.row, 1, 1, s.fields.length).getValues()[0];
    var typed = recordRow_(s.fields, line, s.ref, inputs.data[s.ref] || {}, rendu.data[s.ref]);
    writeJson_(inputs, s.ref, typed);
    redrawRow_(s.sheet, s.fields, s.row, s.ref, typed, calcTab.data[s.ref], calcTab.data.__meta, rendu);
  } finally {
    lock.releaseLock();
  }
}

function releaseSelected() {
  var s = selectedRef_(); if (!s || !s.ref) return;
  var lock = LockService.getDocumentLock();
  if (!lock.tryLock(20000)) { SpreadsheetApp.getUi().alert('Le fichier est occupé, réessayez dans un instant.'); return; }
  try {
    var ss = s.sheet.getParent();
    var inputs = readJsonTab_(ss, '_saisies'), rendu = readJsonTab_(ss, '_rendu'), calcTab = readJsonTab_(ss, '_calcul');
    var typed = inputs.data[s.ref] || {};
    Object.keys(typed).forEach(function (k) { if (isMonthKey(k)) delete typed[k]; });
    writeJson_(inputs, s.ref, typed);
    redrawRow_(s.sheet, s.fields, s.row, s.ref, typed, calcTab.data[s.ref], calcTab.data.__meta, rendu);
  } finally {
    lock.releaseLock();
  }
}

if (typeof module !== 'undefined') {
  module.exports = { distribute: distribute, defaultWeights: defaultWeights, computeRow: computeRow, monthSpan: monthSpan,
                     recordRow_: recordRow_ };
}
