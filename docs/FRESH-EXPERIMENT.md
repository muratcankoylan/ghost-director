# The Conservator: fresh reference experiment

Observed **November 3, 2026, 11:46 PM America/Toronto**. This is a dated experiment record, not a claim that the complete demonstration is finished. The existing Ghost Director application and other productions were preserved.

## What was actually generated

The experiment began in a new isolated production, without importing earlier character, table, perfume, photographic or demo assets. Its three identities are:

| Identity | New photographic reference | Current 3D result |
|---|---|---|
| Mara, a fictional adult conservator | One coherent contact sheet: front full body, back full body and face; terracotta workwear and brown boots | Textured static mesh reconstructed from the approved front view; normalized to an authored height of 1.72 m |
| Cobalt ceramic pitcher | One product photograph of an opaque matte blue pitcher | Textured static mesh reconstructed from the approved primary view; normalized to an authored height of 0.32 m |
| Sunlit cream-plaster studio | One empty-room master photograph with a north window left of centre and a right-wall door | Local Blender architecture adapted through the existing room editor and bound to the approved master; not a whole-room AI reconstruction |

Meta Image generated the three photographic sources. The character views were cropped from one generated sheet; they were not designed through three independent text prompts. Meta SAM 3D Objects, hosted through fal, generated both meshes. Local Blender validated and normalized their embedded GLBs. This test did not use SAM 3D Body, a rigging service or a final-video model.

The host reviewer inspected the actual photographs and approved their exact reference revisions for this limited test. The records explicitly identify `host-test-reviewer`; this does not represent a creative approval by the user. Image consistency is a visual assessment, not a proof of 3D consistency.

## Measured completion and cost

| Evidence at observation time | Result |
|---|---|
| New photograph requests | 3 succeeded |
| Image-to-3D requests | 2 succeeded |
| Application-ledger estimated charges | **$0.07 total**: $0.03 images and $0.04 reconstruction |
| Authorized experiment ceiling | $1; no final-video generation authorized |
| Reconstruction reviews | None yet |
| Character/prop placement in accepted scene | Not yet; both await visual comparison and acceptance |
| Fresh-example Blender motion clips / production packages / movie takes | None yet |

These are application estimates, not provider-account billing statements. A successful reconstruction request proves that a returned GLB was obtained and normalized; it does not prove visual fidelity. The available character dimensions are approximately 0.554 × 0.362 × 1.720 m; the pitcher is approximately 0.416 × 0.273 × 0.320 m. Height is an explicit authoring choice, not a recovered measurement.

## Inspect the workflow in the application

When this isolated production or a separately reviewed export is loaded:

1. Open **References**. Inspect Mara, Cobalt ceramic pitcher and Sunlit cream-plaster studio, their approved photographs and recorded versions.
2. For a reconstructed character or prop, choose **Compare in world**. The existing 3D canvas displays the candidate beside its approved images. Record geometry, proportion, material, color and identity differences before accepting or requesting changes. Placement requires a matching accepted mesh review.
3. For the room, use **Compare location in world** to inspect the approved location master alongside the authored architecture. The location binding expresses design guidance, not proof of reconstruction accuracy.
4. Open **Blender** to inspect supported local subprocess work, shot readiness and available render outputs. An empty history means no such work has been executed in this production; it is not a successful render.
5. After placement and scene review, use **Compose shots**, save one shared plan, render its motion reference, then prepare the exact submission for review. Original approved photos remain appearance inputs; the Blender clip supplies motion guidance.

The browser contains these workflows; this record does not claim that every step has been completed for this new example. Historical successful Blender/video experiments in other productions are separate evidence.

## Reproduce without importing private production data

Follow [installation](../README.md#quick-start) first. A source checkout does not contain generated media, the experiment database or paid-provider receipts. With a configured local Blender executable, initialization is local and does not require model credentials:

```sh
uv run python tools/new_reference_example.py --env-file .env.local init
uv run python tools/new_reference_example.py --env-file .env.local inspect
```

The harness refuses an existing unrelated production. It creates a new empty set and three draft identities; it does not fabricate their photographs. The `generate` and `reconstruct` stages require an explicit, recorded paid authorization. Do not pass the authorization option unless the operator has actually approved the stated cost and scope. `approve-references` and `review-place` require separate JSON assessments identifying the exact revisions or reconstruction IDs. There is deliberately no final-video dispatch stage.

The remaining local stages are `design`, `compose`, `motion` and `compile`. Each uses the existing application services. Newly executed stages record command completion or failure; earlier requests are not backfilled with invented command histories. The checkpoint records new artifact lineage, immutable reference pins, stage outputs and spending state. Generated outputs may differ on another run.

## Known limits

- The character is static, without articulated performance or verified hand topology. Its reconstruction uses the front view; the approved back and face views remain comparison evidence, not additional 3D conditioning inputs.
- Mesh likeness, hidden surfaces and the pitcher's handle/interior require direct inspection. Neither mesh had an accepted visual review at this observation time.
- Room dimensions are authored estimates. One photograph cannot establish a measured room. Floor joints, plaster texture, window detail, exterior foliage and illumination may differ from the master.
- Blender uses the working subprocess integration. Blender MCP is not implemented.
- No final film was generated in this experiment. The preparation UI and earlier provider integrations must not be presented as a finished movie from this fresh scene.

See [reference-first requirements](SPECIFICATION.md), [implementation boundaries](ARCHITECTURE.md), and [release/provenance rules](PROVENANCE.md).
