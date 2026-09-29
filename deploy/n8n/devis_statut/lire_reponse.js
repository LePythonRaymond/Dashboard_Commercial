// Node "Lire la réponse de Furious" (Code, run once for all items).
// Input: the answers of "Furious : changer le statut", paired with the rows of the
// true branch of "Mettre à jour Furious ?". Furious answers HTTP 200 in both cases:
// {"success": true, "id": ...} when the status changed, {"success": false,
// "message": [...]} when it refused (e.g. "BU est requis", "Pipe invalide").
//   ok         "Statut Furious" confirmed, message cleared
//   annuler    refused: status put back, Furious's reason in "Retour Furious"
//   reessayer  no usable answer: row left pending (the next run reads the devis
//              again, so an update that did go through is only confirmed)

const RETRY = 'Furious ne répond pas : nouvel essai automatique dans quelques minutes.';

const decode = (text) => String(text ?? '')
  .replace(/&#0*39;|&apos;/g, "'").replace(/&quot;/g, '"').replace(/&lt;/g, '<')
  .replace(/&gt;/g, '>').replace(/&nbsp;/g, ' ').replace(/&amp;/g, '&');
const pairedIndex = (item, i) => {
  const paired = Array.isArray(item.pairedItem) ? item.pairedItem[0] : item.pairedItem;
  return typeof paired?.item === 'number' ? paired.item : i;
};
const transient = (res) => Boolean(res?.error) || !res?.statusCode || res.statusCode >= 500
  || [401, 403, 408, 429].includes(res.statusCode) || typeof res.body !== 'object' || res.body === null;

function reason(body) {
  const raw = body?.message ?? body?.error ?? '';
  const parts = (Array.isArray(raw) ? raw : [raw])
    .map((part) => decode(typeof part === 'string' ? part : (part?.message ?? JSON.stringify(part))).trim())
    .filter(Boolean);
  return parts.join(' ; ').slice(0, 600) || 'réponse inattendue';
}

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

const rows = $('Mettre à jour Furious ?').all(0);
const out = [];
const settle = (row, action, options) => {
  const properties = notionProperties(row, options);
  if (Object.keys(properties).length) out.push({ json: { ...row, action, notion_body: { properties } } });
};

$input.all().forEach((item, i) => {
  const { furious_body, ...row } = rows[pairedIndex(item, i)].json;
  const res = item.json ?? {};
  if (transient(res)) return settle(row, 'reessayer', { message: RETRY });
  if (res.body.success === true) return settle(row, 'ok', { confirm: true });
  return settle(row, 'annuler', {
    revert: true,
    message: `Furious a refusé le changement : ${reason(res.body)}. Statut remis comme avant.`,
  });
});
return out;
