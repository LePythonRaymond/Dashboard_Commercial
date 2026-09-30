// Node "Préparer les changements" (Code, run once for all items).
// Input, one of:
//   - the page read again after the Notion webhook "Statut modifié" (a page object);
//   - the hourly catch-up: the "Devis à suivre" rows whose "À envoyer à Furious" is
//     checked (a list of pages), i.e. changes the webhook missed (n8n down, Furious
//     not answering, automation paused by Notion after a failed call).
// Keeps the rows of "Devis à suivre" whose status waits for Furious: in scope,
// "Statut Furious" known, "Statut" different from it and not "gagné" (Notion only).
// The same test as the formula "À envoyer à Furious"; it also ignores the webhook
// calls caused by the sync or by n8n itself (then "Statut" = "Statut Furious").
// "Perdu : <raison>" (one status per Furious loss reason, decided 2026-09-30) marks
// the devis lost with that reason; plain "Perdu" is refused (no reason).
// Output: one item per such row. action "envoyer": the Furious pipe (and loss reason)
// and the query that reads the devis. action "annuler": status put back, reason in a
// comment.
// Queries and bodies are built here because they contain "}}", which ends an n8n
// expression.

const FOLLOWUP = ['2ced927802d7802a8642000be714240f', '2ced927802d780e38768e9c9fbeb5815'];   // data source, database
const NOTION_ONLY = ['gagné'];
const PIPES = {
  'brief': 5,
  'en cours': 0,
  'envoyée(s) attente réponse': 4,
  'envoyée(s) en attente de réponse': 4,
};
// Furious loss reasons (tag ids read from the lost devis on 2026-09-30), by the text
// after "Perdu :" in the Notion status. Compared without accents, case or extra spaces.
const LOSS_REASONS = {
  'budget trop élevé': 9,
  'sans réponse du client': 42,
  'projet abandonné': 25,
  'concurrent retenu': 19,
  'garde son prestataire': 155,
  'groupement': 224,
  'doublon': 37,
  'on ne se positionne pas': 50,
  'réponse trop tardive': 211,
  'autre': 20,
};
const LOST_PIPE = 1;

const plain = (prop) => (prop?.rich_text ?? prop?.title ?? []).map((part) => part?.plain_text ?? '').join('').trim();
const key = (name) => String(name ?? '').normalize('NFC').trim().toLowerCase();
const fold = (text) => String(text ?? '').normalize('NFD').replace(/[\u0300-\u036f]/g, '')
  .replace(/[\u2019\u2018]/g, "'").replace(/\s+/g, ' ').trim().toLowerCase();
const REASON_BY_FOLDED = Object.fromEntries(Object.entries(LOSS_REASONS).map(([text, id]) => [fold(text), id]));
const LOSS_CHOICES = Object.keys(LOSS_REASONS).map((text) => `« Perdu : ${text} »`).join(', ');

// { pipe, lost_reason_id } for a status, { refusal } when it cannot be sent, null when unknown.
function target(statut) {
  const pipe = PIPES[key(statut)];
  if (pipe !== undefined) return { pipe };
  const lost = fold(statut).match(/^perdu\s*(?::\s*(.*))?$/);
  if (!lost) return null;
  const reasonId = REASON_BY_FOLDED[fold(lost[1] ?? '')];
  if (reasonId) return { pipe: LOST_PIPE, lost_reason_id: reasonId };
  return { refusal: `pour un devis perdu, choisir la raison dans le statut : ${LOSS_CHOICES}.` };
}
const compact = (id) => String(id ?? '').replace(/-/g, '').toLowerCase();

// The Notion writes of a row: "Statut Furious" confirmed, or "Statut" put back to
// what Furious has; the comment that says why a change was put back.
function notionBody(row, { confirm = false, revert = false }) {
  const properties = {};
  if (confirm) properties['Statut Furious'] = { select: { name: row.statut } };
  if (revert && row.statut_furious) properties['Statut'] = { status: { name: row.statut_furious } };
  return { properties };
}
function commentBody(row, reason) {
  const text = `🔁 Statut remis à « ${row.statut_furious} » : ${reason}`;
  return { parent: { page_id: row.page_id }, rich_text: [{ type: 'text', text: { content: text.slice(0, 1900) } }] };
}

const pages = [];
for (const item of $input.all()) {
  const json = item.json ?? {};
  if (Array.isArray(json.results)) pages.push(...json.results);
  else if (json.object === 'page') pages.push(json);
}

const out = [];
for (const page of pages) {
  const parent = page.parent ?? {};
  if (![parent.data_source_id, parent.database_id].some((id) => FOLLOWUP.includes(compact(id)))) continue;
  if (page.in_trash || page.archived) continue;
  const props = page.properties ?? {};
  const row = {
    page_id: page.id,
    devis_id: plain(props['ID Devis']),
    titre: plain(props['Name']),
    statut: props['Statut']?.status?.name ?? '',
    statut_furious: props['Statut Furious']?.select?.name ?? '',
  };
  const inScope = props['Dans le périmètre']?.checkbox === true;
  if (!inScope || !row.statut_furious || row.statut === row.statut_furious || NOTION_ONLY.includes(key(row.statut))) continue;

  const wanted = target(row.statut);
  row.pipe_cible = wanted?.pipe ?? null;
  if (wanted?.lost_reason_id) row.lost_reason_id = wanted.lost_reason_id;
  let refusal = '';
  if (!/^\d+$/.test(row.devis_id)) {
    refusal = "pas d'ID Devis sur cette ligne.";
  } else if (wanted?.refusal) {
    refusal = wanted.refusal;
  } else if (row.pipe_cible === null) {
    refusal = `« ${row.statut} » ne se choisit pas dans Notion : un devis signé se passe en gagné dans Furious. `
      + 'Dans Notion : brief, en cours, envoyée(s) attente réponse, gagné en attendant la signature, ou Perdu : raison.';
  }
  if (refusal) {
    out.push({ json: { ...row, action: 'annuler', notion_body: notionBody(row, { revert: true }),
                       comment_body: commentBody(row, refusal) } });
    continue;
  }
  const query = `{ Proposal(limit: 1, offset: 0, filter: {id: {eq: "${row.devis_id}"}})`
    + '{ id, statut, pipe, cf_bu, cf_typologie_de_devis, cf_typologie_myrium } }';
  out.push({ json: { ...row, action: 'envoyer', furious_query: query } });
}
return out;
