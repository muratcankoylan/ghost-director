# Architecture and invariants

One FastAPI process, one SQLite production ledger and one React/Three.js application. Blender is a bounded local subprocess. Provider adapters perform durable reservations and one submission per recorded attempt. There is no separate orchestration service or second scene database.

## Responsibilities

- `references.py` owns persistent identities, immutable reference versions, named states and attributed approval. Photographic files remain linked to their originals.
- `reference_reconstruction.py` validates approved inputs, requests textured GLBs, normalizes metric height through Blender and records exact-mesh reviews. New placement requires the latest accepted review for that mesh/hash/reference pin.
- `domain/models.py` owns typed scene and command boundaries. Scene edits preserve identity and protected asset provenance; physical changes create revisions.
- `scene_design.py` and `blender/design_room.py` compile original editable room geometry. Location images guide adaptation; the app does not claim to infer a measured room.
- `motion_models.py`, `motion_math.py` and `motion.py` implement the shared camera/root timeline. The real-time preview and Blender consume the same positions, scales, orientations, times and entity IDs. Units are metres, right-handed Z-up, seconds.
- `shot_workflow.py` verifies actual media, receipts, hashes and approved reference pins; compiles the prompt; and binds approval to the exact immutable request digest. Stale/altered requests cannot reuse approval.
- `providers/runner.py` reserves cost before dispatch, records uncertain outcomes and resumes known jobs without silently repeating POSTs. `jobs/store.py` owns transactional state and artifacts.
- `studio/context.py` selects original bundled role contracts or a separately configured trusted Studio OS checkout. Roles propose; deterministic services validate and publish. Context provenance is explicit.
- `api.py` supplies snapshots, commands, artifact delivery and durable event streaming. Inspection/replay does not submit generation.
- `web/src/terminal/` contains reference management, asset/location comparison, shot planning, actual Blender workflow and exact-request review. One world canvas is shared across those modes.

## Lifecycle

1. Upload or generate original reference photographs; inspect and approve the exact versions.
2. Reconstruct character/prop geometry through the configured service; compare before placement. Bind the location master to locally authored architecture.
3. Save a scene and shared shot plan. Render the minimal Blender motion representation locally.
4. Compile the request from approved photographs plus the exact motion clip and shot-accurate prompt.
5. Inspect inputs, limitations and estimate. Authorize the digest before paid dispatch.
6. Review returned media independently. Generated output is not automatically accepted physical scene truth.

This implementation supports camera motion and rigid root transforms. It does not solve articulated acting, hand contact, accurate unseen geometry, automatic visual-fidelity repair or production-quality digital humans. The [source specification](SPECIFICATION.md) states the larger product target; its presence is not a claim that every requirement is complete.
