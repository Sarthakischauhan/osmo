import { execFileSync } from 'node:child_process';
import { mkdtempSync, rmSync, writeFileSync } from 'node:fs';
import { homedir, tmpdir } from 'node:os';
import path from 'node:path';

// A self-signed code signing identity for local macOS builds. macOS keys Screen Recording and Accessibility
// grants to the app's signing certificate, so every build signed with this one keeps them. See docs/desktop-signing.md.
if (process.platform !== 'darwin') throw new Error('Signing identities are only needed on macOS');
const name = process.argv[2] || 'Osmo Dev';
const keychain = path.join(homedir(), 'Library/Keychains/login.keychain-db');
const identities = () => execFileSync('security', ['find-identity', '-v', '-p', 'codesigning'], { encoding: 'utf8' });
if (identities().includes(`"${name}"`)) {
  console.log(`Code signing identity "${name}" already exists`);
  process.exit(0);
}
const temp = mkdtempSync(path.join(tmpdir(), 'osmo-signing-'));
try {
  const file = (base) => path.join(temp, base);
  writeFileSync(file('cert.cnf'), [
    '[req]', 'distinguished_name = dn', 'x509_extensions = ext', 'prompt = no',
    '[dn]', `CN = ${name}`,
    '[ext]', 'basicConstraints = critical, CA:false', 'keyUsage = critical, digitalSignature', 'extendedKeyUsage = critical, codeSigning',
  ].join('\n'));
  // The system LibreSSL writes a PKCS#12 that `security import` reads; OpenSSL 3's defaults it does not.
  const openssl = (args) => execFileSync('/usr/bin/openssl', args, { stdio: ['ignore', 'ignore', 'inherit'] });
  openssl(['req', '-x509', '-newkey', 'rsa:2048', '-nodes', '-days', '3650', '-config', file('cert.cnf'), '-keyout', file('key.pem'), '-out', file('cert.pem')]);
  openssl(['pkcs12', '-export', '-inkey', file('key.pem'), '-in', file('cert.pem'), '-name', name, '-passout', 'pass:osmo', '-out', file('identity.p12')]);
  execFileSync('security', ['import', file('identity.p12'), '-k', keychain, '-P', 'osmo', '-T', '/usr/bin/codesign'], { stdio: 'inherit' });
  // Trusting it for code signing makes it a valid identity; macOS asks for your login password here.
  execFileSync('security', ['add-trusted-cert', '-p', 'codeSign', '-k', keychain, file('cert.pem')], { stdio: 'inherit' });
} finally {
  rmSync(temp, { recursive: true, force: true });
}
if (!identities().includes(`"${name}"`)) throw new Error(`"${name}" was imported but is not a valid code signing identity`);
console.log(`Created code signing identity "${name}"`);
