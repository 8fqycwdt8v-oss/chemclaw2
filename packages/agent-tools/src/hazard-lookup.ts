import { safeFetch } from './safe-fetch';

const ALLOWED = ['pubchem.ncbi.nlm.nih.gov'];
const MAX_BYTES = 200_000;

/**
 * Hazard / GHS lookup against PubChem PUG REST. Accepts a CAS or a SMILES and
 * returns whatever GHS classification PubChem has on file (pictograms, signal
 * word, hazard statements). No DB, no LLM — pure agent tool. Off-the-shelf
 * per CLAUDE.md.
 */
export const hazardLookupTool = {
  name: 'lookup_hazard',
  description:
    'Look up GHS hazard classification for a compound (by CAS number or SMILES). ' +
    'Returns pictograms, signal word, and hazard statements from PubChem. ' +
    'Use before recommending a compound for synthesis or handling.',
  inputSchema: {
    type: 'object' as const,
    properties: {
      cas_or_smiles: { type: 'string', description: 'CAS number (e.g. 67-64-1) or SMILES' },
      kind: { type: 'string', enum: ['cas', 'smiles'], description: 'Which type of identifier' },
    },
    required: ['cas_or_smiles', 'kind'],
  },
  async execute(input: { cas_or_smiles: string; kind: 'cas' | 'smiles' }) {
    const id = input.cas_or_smiles.trim();
    if (id.length === 0 || id.length > 500) return { error: 'cas_or_smiles is required (≤500 chars)' };

    const path = input.kind === 'cas'
      ? `/rest/pug/compound/name/${encodeURIComponent(id)}/cids/JSON`
      : `/rest/pug/compound/smiles/${encodeURIComponent(id)}/cids/JSON`;

    try {
      const cidRes = await safeFetch(`https://pubchem.ncbi.nlm.nih.gov${path}`, ALLOWED);
      if (!cidRes.ok) return { error: `PubChem CID lookup failed: ${cidRes.status}` };
      const cidBody = (await cidRes.json()) as { IdentifierList?: { CID?: number[] } };
      const cid = cidBody.IdentifierList?.CID?.[0];
      if (!cid) return { error: 'No PubChem record found for that identifier' };

      const ghsRes = await safeFetch(
        `https://pubchem.ncbi.nlm.nih.gov/rest/pug_view/data/compound/${cid}/JSON?heading=GHS+Classification`,
        ALLOWED,
      );
      if (!ghsRes.ok) {
        return { cid, hazards: null, note: `PubChem has no GHS classification for CID ${cid}` };
      }
      const raw = await ghsRes.text();
      if (Buffer.byteLength(raw, 'utf8') > MAX_BYTES) {
        return { error: 'PubChem GHS response exceeds size limit' };
      }
      return { cid, ghs_raw: JSON.parse(raw) };
    } catch (err) {
      return { error: err instanceof Error ? err.message : 'lookup_hazard failed' };
    }
  },
};
