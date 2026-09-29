// Node "Décider" (Code, run once for all items).
// Input: the answers of "Furious : lire le devis", paired with the rows of the true
// branch of "Connexion acceptée ?". For each devis, compares what Furious has now
// with the status chosen in Notion:
//   maj        Furious must change: the update body is prepared (item goes on)
//   a_jour     Furious already has that status: "Statut Furious" is confirmed
//   annuler    the change cannot be made: status put back, message written
//   reessayer  Furious did not answer: row left pending, message written once
// Every update re-sends the three custom fields of the devis (Furious refuses an
// update without them). "Typologie Myrium" is read back truncated ("PA " for
// "PA <= 15 000€"), so it is mapped to its full label.

const WAITING = { '5': 'Brief', '0': 'En cours', '4': 'Envoyée(s) attente réponse' };
const TYPOLOGIE_MYRIUM = [['PA', 'PA <= 15 000€'], ['CH', 'CH >= 15 000€']];
const RETRY = 'Furious ne répond pas : nouvel essai automatique dans quelques minutes.';

const decode = (text) => String(text ?? '')
  .replace(/&#0*39;|&apos;/g, "'").replace(/&quot;/g, '"').replace(/&lt;/g, '<')
  .replace(/&gt;/g, '>').replace(/&nbsp;/g, ' ').replace(/&amp;/g, '&');
const first = (value) => (Array.isArray(value) ? value[0] : value);
const pairedIndex = (item, i) => {
  const paired = Array.isArray(item.pairedItem) ? item.pairedItem[0] : item.pairedItem;
  return typeof paired?.item === 'number' ? paired.item : i;
};
const transient = (res) => Boolean(res?.error) || !res?.statusCode || res.statusCode >= 500
  || [401, 403, 408, 429].includes(res.statusCode) || typeof res.body !== 'object' || res.body === null;

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

const rows = $('Connexion acceptée ?').all(0);
const out = [];
const settle = (row, action, options) => {
  const properties = notionProperties(row, options);
  if (Object.keys(properties).length) out.push({ json: { ...row, action, notion_body: { properties } } });
};

$input.all().forEach((item, i) => {
  const row = { ...rows[pairedIndex(item, i)].json };
  const res = item.json ?? {};
  const found = res.body?.data?.Proposal;
  if (transient(res) || !Array.isArray(found)) return settle(row, 'reessayer', { message: RETRY });

  const proposal = found.find((p) => String(p?.id ?? '') === row.devis_id);
  if (!proposal) {
    return settle(row, 'annuler', { revert: true, message: `Devis ${row.devis_id} introuvable dans Furious : statut remis comme avant.` });
  }
  const pipe = String(proposal.pipe ?? '');
  if (!(pipe in WAITING)) {
    return settle(row, 'annuler', {
      revert: true,
      message: `Ce devis est déjà « ${decode(proposal.statut) || pipe} » dans Furious : statut remis comme avant. `
        + 'La synchro du matin mettra la ligne à jour.',
    });
  }
  if (Number(pipe) === row.pipe_cible) return settle(row, 'a_jour', { confirm: true });

  const customFields = [];
  const bu = decode(first(proposal.cf_bu)).trim();
  if (bu) customFields.push({ name: 'bu', value: bu });
  const typologies = (Array.isArray(proposal.cf_typologie_de_devis)
    ? proposal.cf_typologie_de_devis
    : String(proposal.cf_typologie_de_devis ?? '').split(/\|#\||,/))
    .map((value) => decode(value).trim()).filter(Boolean);
  if (typologies.length) customFields.push({ name: 'typologie_de_devis', value: typologies });
  const myriumRead = decode(first(proposal.cf_typologie_myrium)).trim();
  if (myriumRead) {
    const myrium = TYPOLOGIE_MYRIUM.find(([prefix]) => myriumRead.toUpperCase().startsWith(prefix));
    if (!myrium) {
      return settle(row, 'annuler', {
        revert: true,
        message: `Typologie Myrium « ${myriumRead} » inconnue de l'automatisation : statut remis comme avant, le changer dans Furious.`,
      });
    }
    customFields.push({ name: 'typologie_myrium', value: myrium[1] });
  }
  out.push({
    json: {
      ...row,
      action: 'maj',
      statut_furious_actuel: WAITING[pipe],
      furious_body: { action: 'update', data: { id: Number(row.devis_id), pipe: row.pipe_cible, custom_fields: customFields } },
    },
  });
});
return out;
