// Node "Préparer les changements" (Code, run once for all items).
// Input: the Notion query of "Devis à suivre" rows whose "À envoyer à Furious" is
// checked, i.e. "Statut" was changed in Notion and differs from "Statut Furious".
// Output: one item per row. action "envoyer" when the new status has a Furious
// pipe (with the query that reads the devis); action "annuler" (status put back,
// message in "Retour Furious") otherwise. Bodies and queries are built here
// because they contain "}}", which ends an n8n expression.

const PIPES = {
  'brief': 5,
  'en cours': 0,
  'envoyée(s) attente réponse': 4,
  'envoyée(s) en attente de réponse': 4,
};

const plain = (prop) => (prop?.rich_text ?? prop?.title ?? []).map((part) => part?.plain_text ?? '').join('').trim();
const key = (name) => String(name ?? '').normalize('NFC').trim().toLowerCase();

// Properties to write on the row: "Statut Furious" confirmed, "Statut" put back
// to what Furious has, "Retour Furious" set to the message (only when it changes).
function notionProperties(row, { confirm = false, revert = false, message = '' }) {
  const properties = {};
  if (confirm) properties['Statut Furious'] = { select: { name: row.statut } };
  if (revert && row.statut_furious) properties['Statut'] = { status: { name: row.statut_furious } };
  const text = String(message).slice(0, 1900);
  if (text !== row.retour_actuel) {
    properties['Retour Furious'] = { rich_text: text ? [{ type: 'text', text: { content: text } }] : [] };
  }
  return properties;
}

const out = [];
for (const item of $input.all()) {
  for (const page of item.json.results ?? []) {
    const props = page.properties ?? {};
    const row = {
      page_id: page.id,
      devis_id: plain(props['ID Devis']),
      titre: plain(props['Name']),
      statut: props['Statut']?.status?.name ?? '',
      statut_furious: props['Statut Furious']?.select?.name ?? '',
      retour_actuel: plain(props['Retour Furious']),
    };
    row.pipe_cible = PIPES[key(row.statut)] ?? null;
    let refusal = '';
    if (!/^\d+$/.test(row.devis_id)) {
      refusal = "Pas d'ID Devis sur cette ligne : statut non envoyé à Furious.";
    } else if (row.pipe_cible === null) {
      refusal = `« ${row.statut} » ne se choisit pas dans Notion : un devis perdu ou signé se marque dans Furious. `
        + 'Dans Notion : brief, en cours, envoyée(s) attente réponse, ou gagné en attendant la signature.';
    }
    if (!refusal) {
      const query = `{ Proposal(limit: 1, offset: 0, filter: {id: {eq: "${row.devis_id}"}})`
        + '{ id, statut, pipe, cf_bu, cf_typologie_de_devis, cf_typologie_myrium } }';
      out.push({ json: { ...row, action: 'envoyer', furious_query: query } });
      continue;
    }
    const properties = notionProperties(row, { revert: true, message: refusal });
    if (Object.keys(properties).length) out.push({ json: { ...row, action: 'annuler', notion_body: { properties } } });
  }
}
return out;
