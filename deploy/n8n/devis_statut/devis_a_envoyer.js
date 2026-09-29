// Node "Devis à envoyer" (Code, run once for all items).
// Runs after "Furious : connexion" (one login per run). Returns the rows of the
// true branch of "À envoyer à Furious ?" with action "envoyer" when the login
// worked. When Furious refuses the login (wrong or expired password in the n8n
// credential), the rows get action "connexion_refusee": their status is put back,
// a comment says why, and the run stops in error (e-mail from the Error Workflow).
// Nothing retries the login every hour then: repeated failures could lock the
// Furious account, which the Myrium sync uses too. When Furious does not answer,
// the run fails and the rows stay pending for the hourly catch-up.

const REASON = "n8n ne peut plus se connecter à Furious (identifiants à mettre à jour dans n8n). "
  + 'Changer le statut dans Furious en attendant.';

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

const login = $('Furious : connexion').first().json ?? {};
const rows = $('À envoyer à Furious ?').all(0).map((item) => item.json);
const status = login.statusCode;

if (login.error || !status || status >= 500 || status === 408 || status === 429) {
  const why = status ?? login.error?.message ?? 'pas de réponse';
  throw new Error(`Furious ne répond pas à la connexion (${why}) : nouvel essai au rattrapage de l'heure.`);
}
if (login.body?.success === true && login.body?.token) {
  return rows.map((row) => ({ json: { ...row, action: 'envoyer' } }));
}
return rows.map((row) => ({
  json: { ...row, action: 'connexion_refusee', notion_body: notionBody(row, { revert: true }),
          comment_body: commentBody(row, REASON) },
}));
