import { copyFile, mkdir } from 'node:fs/promises';
import { fileURLToPath } from 'node:url';
import { dirname, resolve } from 'node:path';

const frontendRoot = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const source = resolve(frontendRoot, '..', 'viewer-sdk', 'host.js');
const destinationDirectory = resolve(frontendRoot, 'public', 'viewer-sdk');
await mkdir(destinationDirectory, { recursive: true });
await copyFile(source, resolve(destinationDirectory, 'host.js'));
