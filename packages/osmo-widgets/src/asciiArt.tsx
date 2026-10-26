/** Hand-set, equal-width character art. Shared by the web and Electron renderers. */
export const ASCII_ART = {
  sun: [
    '        |        ',
    '    \\   |   /    ',
    '     .-"""-.     ',
    ' -- (  . .  ) -- ',
    '     \\  -  /     ',
    '      `---\u0027      ',
    '    /   |   \\    ',
    '        |        ',
  ].join('\n'),
  moon: [
    '          .      ',
    '    .-""-.   +   ',
    '   /     .\u0027      ',
    '  |     /        ',
    '  |     \\    .   ',
    '   \\     `-..    ',
    '    `-....-\u0027     ',
    '  +              ',
  ].join('\n'),
  cloud: [
    '                 ',
    '      .--.       ',
    '   .-(    ).     ',
    '  (   .--.  )    ',
    ' (___(____)__)   ',
    '                 ',
    '                 ',
    '                 ',
  ].join('\n'),
  rain: [
    '                 ',
    '      .--.       ',
    '   .-(    ).     ',
    '  (___(____)_)   ',
    '    /  /  /      ',
    '   /  /  /       ',
    '     /  /        ',
    '                 ',
  ].join('\n'),
  snow: [
    '                 ',
    '      .--.       ',
    '   .-(    ).     ',
    '  (___(____)_)   ',
    '    *   +   *    ',
    '      *   +      ',
    '    +   *   *    ',
    '                 ',
  ].join('\n'),
  storm: [
    '                 ',
    '      .--.       ',
    '   .-(    ).     ',
    '  (___(____)_)   ',
    '       /_/       ',
    '      /_         ',
    '       /         ',
    '      /          ',
  ].join('\n'),
  wind: [
    '                 ',
    '           .-.   ',
    '  --------\u0027  )  ',
    '      .------\u0027   ',
    '  ---\u0027           ',
    '  ---------.     ',
    '           `-\u0027   ',
    '                 ',
  ].join('\n'),
  stars: [
    '                 ',
    '    .       +    ',
    '        |        ',
    '      --+--      ',
    '        |     .  ',
    '  +              ',
    '          .      ',
    '                 ',
  ].join('\n'),
} as const;

export type AsciiArtName = keyof typeof ASCII_ART;
export const ASCII_ART_NAMES = Object.keys(ASCII_ART) as AsciiArtName[];

export function AsciiArt({ name, className = '', label }: { name: AsciiArtName; className?: string; label?: string }) {
  return <pre className={`osmo-ascii osmo-ascii-${name} ${className}`} role={label ? 'img' : undefined}
    aria-label={label} aria-hidden={label ? undefined : true}>{ASCII_ART[name]}</pre>;
}
