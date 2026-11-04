# The Conservator: fresh reference experiment

[View the illustrated report and screen recording](SHOWCASE.md) for current comparisons, Blender playback and production review.

Initial publication checkpoint: **November 3, 2026, 11:46 PM America/Toronto**. The sections through Known limits preserve that observation; the dated follow-up below records subsequent local work. The existing Ghost Director application and other productions were preserved.

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

Follow [installation](../README.md#quick-start) first. The checkout includes selected documentation images and videos, but not the experiment database, complete generated assets or paid-provider receipts. Its visual report does not populate the application's production state. With a configured local Blender executable, initialization is local and does not require model credentials:

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

## November 4 follow-up: planning review and placement

Observed **November 4, 2026, 12:14 AM America/Toronto (05:14 UTC)**. This follow-up records actual local mesh placement, corrected Blender output and request compilation. Browser verification of this updated production is a separate check and was pending at this checkpoint.

After the publication checkpoint, the host visually inspected both actual reconstructed meshes against their approved images. The command records identify `local-operator`; each note explicitly attributes the review to the **host test reviewer (Codex), not the user**, and limits acceptance to static wide-shot framing and blocking. This is planning acceptance, not certification of photographic identity or final-film appearance.

| Asset | Recorded review | Placement and remaining limits |
| --- | --- | --- |
| Mara | Geometry and proportions match at planning scale; material, color and identity remain unknown | Placed at `(0, 0.45, 0)` m. Face detail is coarse and fingers are fused or insufficiently separated. Clothing appears darker under current warm lighting. Close-up likeness, hand detail, articulated acting and contact are not established. |
| Cobalt pitcher | Geometry, proportions and recognizable object identity match at planning scale; material and color remain unknown | Placed at `(0.7, 0.2, 0)` m. The rounded body, opening, pouring lip and handle are present. Interior thickness, underside and hidden surfaces remain unverified; calibrated ceramic/color fidelity, pouring and hand contact are not claimed. |

The accepted review records are `reconstruction-review-499bd8dec4b94a23` and `reconstruction-review-4d26c3ae76d64a05`. Each placed entity retains the exact review, normalized mesh hash and approved reference-version binding. The photographs remain the appearance source; acceptance does not replace them with reconstructed geometry.

The location binding now carries typed adaptation notes visible in **Compare location in world**. The authored room is 6 × 5 × 3.2 m, with one north-wall window left of centre and an east-wall door. Its dimensions are estimates. Plaster grain, limestone slab seams, precise window recess/mullions, exterior foliage and the photographed sunlight remain unmatched. This is local reference-guided architecture, not AI room reconstruction.

The first local 12-second Blender motion render and compiled request exposed a real framing problem: the floor-level pitcher was cropped in the inspected first, middle and last frames. That attempt remains recorded under `prior_attempts`, with its discrepancy; it is not represented as a successful composition. Before rerendering, the revised camera path passed 4,624 actor/prop bounding-box corner projections over 289 sampled times with a 20-pixel image-edge margin. Combined bounds were approximately x=261.83–400.73 and y=25.61–317.54 in a 640 × 360 image. This geometric check addresses clipping, not photographic fidelity or occlusion.

The corrected render was then inspected at its start, midpoint and end. Both the actor and pitcher technical proxies are fully inside the frame in all three samples. The camera moves while the character and prop remain stationary; this is a blocking reference, not a photorealistic movie or articulated performance. No full-frame-by-frame appearance evaluation is claimed.

| Corrected local evidence | Recorded result |
| --- | --- |
| Accepted scene / shared shot plan | `scene-fadbf7b20a754083` / `shot-6ecf56621ac74d8c` |
| Motion record | `motion-aebba79e64a84220` |
| Actual motion output | 12 seconds; 288 frames at 24 FPS; 640 × 360 H.264 MP4 |
| Local Blender subprocess elapsed time | 11.016 seconds, including render and encoding |
| Sampled Blender channel agreement | Maximum absolute error `1.1920928955078125e-7`, below `1e-4` tolerance, at 3 sampled times |
| Motion MP4 / editable Blender scene | `art-5c06304c-d748-43b4-a106-0303d7c75b15` / `art-757345f6-44da-4ac4-bec5-ddc58e30d42c` |
| Compiled request | `package-3f6d54831d6942e3` |

The compiled request binds the same scene and shot to **five approved images** (three character views, the prop and the location), the corrected actual motion MP4 and the corresponding prompt. Its appearance-coverage validator reports zero missing-binding warnings. This verifies input coverage and lineage; it does not establish the fidelity of a generated movie. The request has been prepared locally, **not approved or dispatched**. Its recorded final-video estimate is approximately **$3.095**, with a **$3.714 reservation**; this is outside the experiment's $1 approval and is not a charge already incurred.

These local review, placement, room and camera operations added no provider requests. The ledger still records five successful paid requests and **$0.07 estimated total**, with no final-video generation.

### Browser verification after the follow-up

The updated application was tested at `http://127.0.0.1:8006/` with paid calls disabled. Both placed meshes rendered in the saved set; character and prop comparisons displayed their persisted planning-only review notes, and the location comparison displayed the approved master, dimensions and remaining fidelity gaps. The Blender panel played the actual 12-second clip, served the `.blend` file and opened the matching validation receipt. Submission review loaded all five approved photographs, the same motion clip and the exact prompt; generation approval remained unchecked. Reload automatically selected the shot bound to the current scene and exposed its matching output. One WebGL canvas remained active, with no asset-load failures. This confirms accessible local workflow behavior, not final-film fidelity.
