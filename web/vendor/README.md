# Local 3D renderer

- Three.js **0.180.0**, including OrbitControls, from the official npm `three` package.
- Upstream: https://github.com/mrdoob/three.js/tree/r180
- License: MIT; see [THREE-LICENSE.txt](THREE-LICENSE.txt).
- `three-graph.min.js` is generated from `scripts/three-entry.mjs` with esbuild **0.25.10**. Versions and package integrity are pinned in `package-lock.json`.
- Rebuild from the repository root with `npm ci --ignore-scripts --no-audit --no-fund` and `npm run build:graph`.

The bundle is served by the local Python app. Normal app startup needs neither Node.js nor a CDN connection. Do not edit the generated bundle directly.
