// Node "Devis à envoyer" (Code, run once for all items).
// Runs after "Furious : connexion" (one login per run). Returns the rows of the
// true branch of "À envoyer à Furious ?" with action "envoyer" when the login
// worked. When Furious refuses the login (wrong or expired password in the n8n
// credential), the rows get action "connexion_refusee": their status is put back
// and the run stops in error, so n8n does not retry the login every two minutes
// (repeated failures could lock the Furious account, which the Myrium sync uses
// too). When Furious does not answer, the run fails and the rows stay pending.

const MESSAGE = 'n8n ne peut plus se connecter à Furious (identifiants à mettre à jour dans n8n) : '
  + 'statut remis comme avant. Le changer dans Furious en attendant.';

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

const login = $('Furious : connexion').first().json ?? {};
const rows = $('À envoyer à Furious ?').all(0).map((item) => item.json);
const status = login.statusCode;

if (login.error || !status || status >= 500 || status === 408 || status === 429) {
  const why = status ?? login.error?.message ?? 'pas de réponse';
  throw new Error(`Furious ne répond pas à la connexion (${why}) : nouvel essai au prochain passage.`);
}
if (login.body?.success === true && login.body?.token) {
  return rows.map((row) => ({ json: { ...row, action: 'envoyer' } }));
}
return rows.map((row) => ({
  json: { ...row, action: 'connexion_refusee', notion_body: { properties: notionProperties(row, { revert: true, message: MESSAGE }) } },
}));
