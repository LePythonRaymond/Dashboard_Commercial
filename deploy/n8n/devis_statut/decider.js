// Node "Décider" (Code, run once for all items).
// Input: the answers of "Furious : lire le devis", paired with the rows of the true
// branch of "Connexion acceptée ?". For each devis, compares what Furious has now
// with the status chosen in Notion:
//   maj        Furious must change: the update body is prepared (item goes on)
//   a_jour     Furious already has that status: "Statut Furious" is confirmed
//   annuler    the change cannot be made: status put back, reason in a comment
//   (none)     Furious did not answer: nothing written, the row stays pending and
//              the hourly catch-up tries again
// Every update re-sends the three custom fields of the devis (Furious refuses an
// update without them). "Typologie Myrium" has three options, "PA <= 15 000€",
// "CH >= 15 000€" and "PA"; the API cuts a value at "<", so "PA <= 15 000€" is read
// back as "PA " (with its space) while the option "PA" reads "PA". A value that
// cannot be told apart is never sent.
// A loss ("Perdu : <raison>") is sent as pipe 1 with the Furious loss reason; a devis
// already lost in Furious is only confirmed.

const WAITING = { '5': 'Brief', '0': 'En cours', '4': 'Envoyée(s) attente réponse' };
const LOST_PIPE = '1';

function typologieMyrium(read) {
  const raw = String(read ?? '');
  const text = raw.trim().toUpperCase();
  if (!text) return { value: null };
  if (text.startsWith('CH')) return { value: 'CH >= 15 000€' };
  if (text === 'PA' && raw === raw.trimEnd()) return { value: 'PA' };
  if (text.startsWith('PA')) return { value: 'PA <= 15 000€' };
  return { unknown: raw.trim() };
}

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

const rows = $('Connexion acceptée ?').all(0);
const out = [];
const settle = (row, action, { confirm = false, revert = false, reason = '' } = {}) => {
  const json = { ...row, action, notion_body: notionBody(row, { confirm, revert }) };
  if (reason) json.comment_body = commentBody(row, reason);
  out.push({ json });
};

$input.all().forEach((item, i) => {
  const row = { ...rows[pairedIndex(item, i)].json };
  const res = item.json ?? {};
  const found = res.body?.data?.Proposal;
  if (transient(res) || !Array.isArray(found)) return;   // pending: the hourly catch-up tries again

  const proposal = found.find((p) => String(p?.id ?? '') === row.devis_id);
  if (!proposal) return settle(row, 'annuler', { revert: true, reason: `devis ${row.devis_id} introuvable dans Furious.` });
  const pipe = String(proposal.pipe ?? '');
  if (row.pipe_cible === Number(LOST_PIPE) && pipe === LOST_PIPE) return settle(row, 'a_jour', { confirm: true });
  if (!(pipe in WAITING)) {
    return settle(row, 'annuler', {
      revert: true,
      reason: `ce devis est déjà « ${decode(proposal.statut) || pipe} » dans Furious (la synchro du matin mettra la ligne à jour).`,
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
  const myrium = typologieMyrium(decode(first(proposal.cf_typologie_myrium)));
  if (myrium.unknown) {
    return settle(row, 'annuler', {
      revert: true,
      reason: `Typologie Myrium « ${myrium.unknown} » inconnue de l'automatisation, changer le statut dans Furious.`,
    });
  }
  if (myrium.value) customFields.push({ name: 'typologie_myrium', value: myrium.value });
  const data = { id: Number(row.devis_id), pipe: row.pipe_cible };
  if (row.lost_reason_id) data.lost_reason_id = row.lost_reason_id;
  out.push({
    json: {
      ...row,
      action: 'maj',
      statut_furious_actuel: WAITING[pipe],
      furious_body: { action: 'update', data: { ...data, custom_fields: customFields } },
    },
  });
});
return out;
