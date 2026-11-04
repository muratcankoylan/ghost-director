# Verification

Release checkpoint, November 3, 2026: the standalone checkout passed **316 backend tests** (two optional external Studio OS integration tests skipped), **114 frontend tests** and the production web build. A clean local bootstrap and browser smoke test passed with live calls disabled: the procedural room, empty reference registry and actual Blender workflow interface were accessible, with no browser console errors. Thirty local Blender geometry raycasts passed. These are local results; GitHub Actions reports its own run status.

November 4 follow-up: **318 backend tests** (the same two optional skips), **121 frontend tests** and the web build passed. New projection regressions reproduce tall-asset clipping in the previous frame-selection shortcut and floor-prop clipping in the initial test camera path. Corrected framing respects the current lens/aspect/clipping planes; the test path keeps all 16 subject bounding-box corners inside a 20-pixel margin over 289 timestamps. Startup chooses a shot bound to the current scene without overriding a deliberate historical selection. The corrected local Blender clip is 12 seconds, 288 frames, 24 FPS, 640×360; sampled transform error is 1.19e-7 against a 1e-4 tolerance. This is technical motion footage, not final generated film.

Run `uv run python -m pytest tests/ghost_director`, `npm --prefix web test` and `npm --prefix web run build` from this checkout. CI executes those checks with live calls disabled. Test fixtures are not claims of successful provider generation.

Meaningful checks cover approval/hash binding, stale revisions, malformed media, duplicate dispatch prevention, unknown outcomes, budget reservations, reconstruction review, reference pins, export/import and replay. UI tests cover reference comparison, state changes, approval gating, one canvas and Blender controls. Real external Studio OS context tests require that separate checkout; bundled public-contract tests are independent.

Local geometry verification can run through Blender:

```sh
blender --background --python tests/ghost_director/scene/blender_door_geometry.py
```

It checks clear openings, thresholds, opposite walls, lintels and piers for both the authored set and minimal motion scene. It requires Blender and is separate from ordinary pytest.

The previously measured local motion integration produced a 15-second, 360-frame, 24-FPS, 640×360 MP4, with maximum sampled channel error 1.19e-7 against a 1e-4 tolerance. That clip belongs to an earlier local production and is not included here. The fresh Conservator scene's unfinished stages are explicitly recorded in `FRESH-EXPERIMENT.md`.

No paid API is required by the automated test suite. A live quality evaluation must record service/model, input hashes, authorization, estimated/reported costs, actual outputs and limitations. Do not turn a mocked test into a live-generation claim.
