# Scene authoring and reference rendering

Canonical scene coordinates are right-handed Z-up meters. Entity Euler angles
are XYZ radians. Exported GLBs are local Y-up meters; the browser applies
`C * R * S * C^-1` plus the converted translation exactly once, where
`C(x,y,z) = (x,z,-y)`. Blender's GLB importer already restores Z-up coordinates.

`build_set.py` authors the room and seven prop assets, imports original actor
assemblies through `asset-map.json`, and saves a packed `dinner-set.blend` cache.
The geometry is project-authored; named packed CC0 Poly Haven texture components
come from the original production scene. Source URLs and file hashes are retained
in `asset-manifest.json`. No mannequin geometry is reused.

```sh
BLENDER=/path/to/Blender.app/Contents/MacOS/Blender
"$BLENDER" --background --python ghost_director/blender/build_set.py -- \
  --scene productions/ghost-director-hackathon/assets/scene.template.json \
  --asset-version v2 --require-actors --render --samples 32 --resolution 960
```

`render_scene.py` is the production reference path. It opens a pinned cache,
applies the supplied revision's entity transforms, visibility, explicit material
overrides, lights, and cameras, then reloads actors from the pinned GLB map. It
does not regenerate assets or call providers. A missing visible asset is an error.
Actor map entries must resolve to GLBs inside the supplied asset directory.

```sh
"$BLENDER" --background --python ghost_director/blender/render_scene.py -- \
  --scene /path/to/pinned-scene.json --asset-root /path/to/pinned-assets \
  --output /path/to/reference-output --camera-id GDR-DP-CAM-001 \
  --samples 32 --resolution 960
```

Output is `<camera-id>.png` plus `render-receipt.json`. The receipt records scene
and mapping hashes, Blender version, actual Cycles light energy, and render hashes.
The caller owns immutable publication and must pin the cache, map, and GLBs
together. The cache contains packed textures and was tested in a separate folder
without the source texture files.

Camera projection uses horizontal sensor fit, explicit sensor width and focal
length, near/far clips, and the requested aspect ratio. Three.js camera mode
letterboxes instead of changing the lens. The independent pinhole oracle in
`camera_math.py` is tested against actual Blender camera projections.

## Material and light semantics

Native GLB materials remain intact unless `state.material_override` is true.
An override tints native color textures and sets roughness/metalness multipliers.

Scene light intensity is authored strength, not a shared physical unit. The
preview uses point/directional sources. Cycles uses soft area sources with fixed
gains in `lighting.py`: point strength × 1.4 W and directional strength × 120 W.
Both apply canonical positions, visibility, color and target. Cycles also has a
fixed 90 W room-bounce source and a low ambient world. This is an explicit preview
calibration, not a claim of pixel-identical photometric rendering.

## Original actor assemblies

`normalize_actor.py` records the source hash, explicit orientation correction,
metric dimensions, and texture transformations. Seated heights are 1.32 m and
1.29 m. The origin is the floor beneath the torso median, excluding forward knees.

The v1 Meta SAM3D experiment is preserved. It had weak face detail and Eren had
waist tunnels. The v2 comparison uses the same original Meta images and SAM masks,
then Meshy 7.1 geometry. Its generated chairs are connected to the actors; the
actor records explicitly include the corresponding chair entity and the separate
authored chair is hidden. These assemblies are static and have no skeleton. An
independent actor/chair transform is not supported for this asset version.

Four-angle and facial inspection evidence is under production `experiments/`.
v2 is a candidate for medium shots: faces, hands and clothing are materially
clearer than v1, but solid hair and baked eye detail remain close-up limitations.
Successful parsing or rendering is never treated as visual acceptance.

## Verification

Run `pytest tests/ghost_director/scene -q` and, inside `web`,
`vitest run src/viewport`. Browser evidence additionally
covers real asset loading, selection, orbit/camera/walk modes and pointer release.
The viewport exposes actual render counters on
`.gd-viewport-canvas[data-render-metrics]`; these are diagnostic measurements,
not simulated status. Collision bounds are rebuilt only when assets/transforms
change. Walking entry clamps the camera to the room margin and searches for a
nearby clear floor position when a composed view starts inside furniture. Missing
actor meshes remain visibly missing, with no proxy replacement.

Limitations: room dimensions are authored during `build_set.py`; changing the
room's dimensions record requires rebuilding its versioned geometry. A cached
render applies room entity transforms but does not silently remodel walls.
