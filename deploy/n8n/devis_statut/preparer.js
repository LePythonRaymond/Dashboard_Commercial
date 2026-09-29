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
// Output: one item per such row. action "envoyer": the Furious pipe and the query
// that reads the devis. action "annuler": status put back, reason in a comment.
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

const plain = (prop) => (prop?.rich_text ?? prop?.title ?? []).map((part) => part?.plain_text ?? '').join('').trim();
const key = (name) => String(name ?? '').normalize('NFC').trim().toLowerCase();
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

  row.pipe_cible = PIPES[key(row.statut)] ?? null;
  let refusal = '';
  if (!/^\d+$/.test(row.devis_id)) {
    refusal = "pas d'ID Devis sur cette ligne.";
  } else if (row.pipe_cible === null) {
    refusal = `« ${row.statut} » ne se choisit pas dans Notion. Perdu et gagné (signé) se marquent dans Furious ; `
      + 'dans Notion : brief, en cours, envoyée(s) attente réponse, ou gagné en attendant la signature.';
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
