/*
  Party identity colours.

  Used in exactly one place: the honours list, to colour the party that tops a column. Every
  other mark on the site stays a single neutral hue, because a chart coloured by party turns
  a reading of declared facts into a display of allegiance.

  These are the colours each party uses on its own flag and material. They are recognisable
  to an Indian reader at a glance, which is the point — the joke lands faster when you know
  who is being handed the certificate before you read the name.

  Anything not listed falls back to the neutral data colour rather than being assigned a
  hue automatically. A generated colour would imply an identity the party has not chosen.
*/

export const PARTY_COLOURS = {
  BJP: '#F47216',
  INC: '#00BFFF',
  AITC: '#20603D',
  DMK: '#E4002B',
  SP: '#D2232A',
  TDP: '#F5C518',
  'JD(U)': '#2E8B57',
  RJD: '#177C3C',
  'CPI(M)': '#CC0000',
  CPI: '#CC0000',
  BSP: '#22409A',
  AAP: '#009DDC',
  YSRCP: '#1560BD',
  'SHS(UBT)': '#F47216',
  'ShivSena (Uddhav Balasaheb Thackrey)': '#F47216',
  ShivSena: '#F47216',
  SHS: '#F47216',
  NCP: '#00A0E3',
  'NCP(SP)': '#00A0E3',
  JMM: '#009933',
  IUML: '#008000',
  'AIADMK': '#009933',
  AIMIM: '#007A3D',
  BJD: '#008000',
  'JD(S)': '#138808',
  AGP: '#F47216',
  RLD: '#4CAF50',
  'LJP(RV)': '#5C2D91',
  IND: '#7D8087',
};

/** The neutral fallback: an unlisted party gets the site's own data colour, not a guess. */
export const NEUTRAL = 'var(--data)';

export function partyColour(party) {
  return PARTY_COLOURS[party] ?? NEUTRAL;
}

/** Whether a party has a real identity colour, as opposed to falling back to neutral. */
export function hasPartyColour(party) {
  return Object.hasOwn(PARTY_COLOURS, party);
}
