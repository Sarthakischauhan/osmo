/** Time-of-day illustrations rendered directly onto a fixed character grid.
 * Four samples per axis keep curved edges smooth without changing font or layout.
 */
export type Band = 'dawn' | 'morning' | 'midday' | 'dusk' | 'evening' | 'night';
const WIDTH = 64;
const HEIGHT = 20;
const RAMP = ' .:-=+*#%@';
const CRATERS = [[-2.7, -2.4, 1.3], [2.3, 1.4, 1.8], [-1.7, 3.2, .9], [3, -3.3, .7]] as const;

function frame(band: Band): string {
  const horizon = band === 'dawn' || band === 'dusk';
  const moon = band === 'evening' || band === 'night';
  const cy = horizon ? 13.8 : 9.5;
  const radius = band === 'night' ? 6.8 : 6.2;
  const edge = band === 'dusk' ? 12 : 13;
  function sample(column: number, row: number): number {
    // Terminal cells are taller than they are wide.
    const x = (column - WIDTH / 2) * .48;
    const y = row - cy;
    const distance = Math.hypot(x, y);
    if (horizon && row > edge) {
      if (Math.abs(row - edge - .2) < .12) return .45;
      const spread = Math.max(.5, 8 - (row - edge) * 1.2);
      if (Math.abs(x) < spread && Math.sin(column * 1.7 + row * 4.3) > .35) return .12;
      return 0;
    }
    if (distance <= radius) {
      if (band === 'evening' && Math.hypot(x - 3.3, y + 1.2) < radius * .97) return 0;
      const normal = Math.sqrt(Math.max(0, 1 - (distance / radius) ** 2));
      let shade = moon ? .45 + normal * .48 - x * .015 : .52 + normal * .47;
      if (band === 'night') for (const [cx, craterY, r] of CRATERS) {
        const d = Math.hypot(x - cx, y - craterY);
        if (d < r) shade -= .24 * (1 - d / r);
      }
      return shade;
    }
    if (!moon && band !== 'dusk') {
      const angle = Math.atan2(y, x);
      const fromRay = Math.abs(Math.sin(angle * 4));
      if (distance > radius + 1.7 && distance < radius + 3.5 && fromRay < .065) return .62;
    }
    return 0;
  }
  const rows: string[][] = [];
  for (let row = 0; row < HEIGHT; row++) {
    const cells: string[] = [];
    for (let col = 0; col < WIDTH; col++) {
      let light = 0;
      for (let sy = 0; sy < 4; sy++) for (let sx = 0; sx < 4; sx++) light += sample(col + (sx + .5) / 4, row + (sy + .5) / 4);
      cells.push(RAMP[Math.min(RAMP.length - 1, Math.round(light / 16 * (RAMP.length - 1)))] ?? ' ');
    }
    rows.push(cells);
  }
  if (moon) {
    const stars = [[6, 3, '+'], [15, 15, '.'], [52, 4, '*'], [57, 14, '+'], [10, 9, '.'], [47, 17, '.'], [22, 1, '.']] as const;
    for (const [x, y, glyph] of stars) if (rows[y]?.[x] === ' ') rows[y]![x] = glyph;
  }
  return rows.map(row => row.join('')).join('\n');
}

export const DAWN = frame('dawn');
export const MORNING = frame('morning');
export const MIDDAY = frame('midday');
export const DUSK = frame('dusk');
export const EVENING = frame('evening');
export const NIGHT = frame('night');
export const FRAMES: Record<Band, string> = { dawn: DAWN, morning: MORNING, midday: MIDDAY, dusk: DUSK, evening: EVENING, night: NIGHT };
export const BAND_LABEL: Record<Band, string> = { dawn: 'Dawn', morning: 'Morning', midday: 'Midday', dusk: 'Dusk', evening: 'Evening', night: 'Night' };
/** Local-time band. dawn 05-07, morning 07-11, midday 11-16, dusk 16-19, evening 19-21, night 21-05. */
export function getBand(date: Date = new Date()): Band {
  const m = date.getHours() * 60 + date.getMinutes();
  if (m >= 300 && m < 420) return 'dawn';
  if (m >= 420 && m < 660) return 'morning';
  if (m >= 660 && m < 960) return 'midday';
  if (m >= 960 && m < 1140) return 'dusk';
  if (m >= 1140 && m < 1260) return 'evening';
  return 'night';
}
