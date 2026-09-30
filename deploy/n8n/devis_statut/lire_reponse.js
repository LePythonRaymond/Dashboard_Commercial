// Node "Lire la réponse de Furious" (Code, run once for all items).
// Input: the answers of "Furious : changer le statut", paired with the rows of the
// true branch of "Mettre à jour Furious ?". Furious answers HTTP 200 in both cases:
// {"success": true, "id": ...} when the status changed, {"success": false,
// "message": [...]} when it refused (e.g. "BU est requis", "Pipe invalide").
//   ok        "Statut Furious" confirmed; for a loss also "Perdu le" = today (Paris):
//             Furious keeps the old devis date when a devis is lost through its API,
//             Myrium uses this day as the loss date in "Devis perdus"
//   annuler   refused: status put back, Furious's reason in a comment
//   (none)    no usable answer: nothing written, the row stays pending; the hourly
//             catch-up reads the devis again, so an update that did go through is
//             only confirmed then

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

function notionBody(row, { confirm = false, revert = false }) {
  const properties = {};
  if (confirm) properties['Statut Furious'] = { select: { name: row.statut } };
  if (revert && row.statut_furious) properties['Statut'] = { status: { name: row.statut_furious } };
  return { properties };
}
function commentBody(row, text) {
  const content = `🔁 Statut remis à « ${row.statut_furious} » : ${text}`;
  return { parent: { page_id: row.page_id }, rich_text: [{ type: 'text', text: { content: content.slice(0, 1900) } }] };
}

function parisToday() {
  try {
    return new Intl.DateTimeFormat('sv-SE', { timeZone: 'Europe/Paris' }).format(new Date());   // YYYY-MM-DD
  } catch (error) {
    return new Date().toISOString().slice(0, 10);
  }
}

const rows = $('Mettre à jour Furious ?').all(0);
const out = [];

$input.all().forEach((item, i) => {
  const { furious_body, ...row } = rows[pairedIndex(item, i)].json;
  const res = item.json ?? {};
  if (transient(res)) return;   // pending: the hourly catch-up tries again
  if (res.body.success === true) {
    const notion_body = notionBody(row, { confirm: true });
    if (row.pipe_cible === 1) notion_body.properties['Perdu le'] = { date: { start: parisToday() } };
    out.push({ json: { ...row, action: 'ok', notion_body } });
    return;
  }
  out.push({ json: { ...row, action: 'annuler', notion_body: notionBody(row, { revert: true }),
                     comment_body: commentBody(row, `Furious a refusé le changement (${reason(res.body)}).`) } });
});
return out;
