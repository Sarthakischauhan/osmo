// Module hooks for app.test.js: `electron` resolves to the stand-in next to this file, so src/app.js runs without Electron.
export async function resolve(specifier, context, next) {
  if (specifier === 'electron') return { url: new URL('./electron-stub.js', import.meta.url).href, shortCircuit: true };
  return next(specifier, context);
}
