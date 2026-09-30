import {build} from 'esbuild';
import {copyFile, mkdir} from 'node:fs/promises';

await mkdir('web/vendor', {recursive: true});
await build({
  entryPoints: ['scripts/three-entry.mjs'],
  outfile: 'web/vendor/three-graph.min.js',
  bundle: true, minify: true, format: 'iife', globalName: 'ObsiThree',
  target: ['es2020'], legalComments: 'eof'
});
await copyFile('node_modules/three/LICENSE', 'web/vendor/THREE-LICENSE.txt');
