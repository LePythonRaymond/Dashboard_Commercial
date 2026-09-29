// Node "Page modifiée" (Code, run once for all items).
// Input: the call made by the Notion automation "Statut modifié" of "Devis à suivre"
// (Send webhook). Only the page id is kept: the next node reads the page again from
// Notion, so a late, repeated or forged call can only make n8n look at what Notion
// holds now. Notion does not document the payload; the page is expected in
// body.data (a page object), with a search of the whole payload as a fallback.
// No page found: the run fails, so the Error Workflow reports it.

const UUID = /^[0-9a-f]{8}-?[0-9a-f]{4}-?[0-9a-f]{4}-?[0-9a-f]{4}-?[0-9a-f]{12}$/i;

function findPageId(payload) {
  for (const candidate of [payload?.data?.id, payload?.data?.page_id, payload?.page_id]) {
    if (typeof candidate === 'string' && UUID.test(candidate)) return candidate;
  }
  const stack = [payload];
  while (stack.length) {
    const node = stack.pop();
    if (!node || typeof node !== 'object') continue;
    if (node.object === 'page' && typeof node.id === 'string' && UUID.test(node.id)) return node.id;
    stack.push(...Object.values(node));
  }
  return null;
}

const seen = new Set();
const out = [];
for (const item of $input.all()) {
  const payload = item.json.body ?? item.json;
  const pageId = findPageId(payload);
  if (!pageId) {
    throw new Error(`Appel du webhook sans page Notion reconnaissable : ${JSON.stringify(payload).slice(0, 300)}`);
  }
  const key = pageId.replace(/-/g, '').toLowerCase();
  if (!seen.has(key)) {
    seen.add(key);
    out.push({ json: { page_id: pageId } });
  }
}
return out;
