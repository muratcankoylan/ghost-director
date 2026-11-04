# Terminal UI implementation review

Verified locally on November 3, 2026. These screenshots show the running application and real production records, not an authored product mockup. The fixture module in this directory is used only by automated tests and explicitly identifies itself as a fixture.

## Implemented boundary

`TerminalApp` receives the generated `Snapshot`, command callback, connection state, saved-run replay callback, and an injected `SceneViewport`. The client owns navigation, inspector pinning, prompt/camera/entity drafts, and media playback. Provider credentials and direct provider calls remain outside the client.

The permanent layout includes the world, three-shot sequence, selected video take, Meta result, model desk, task/handoff activity, command composer, and inspector. Prompt/input/output summaries stay in one inspector. Task selection can show the actual corresponding Meta output in the output shelf. Explicit take IDs and source packages govern review commands; the active generation shot remains independent of pinned inspection. Video labels come from recorded package/job/artifact metadata, so changing the configured model does not relabel historical takes. The active video binding supplies the fixed reference-mode duration when saving a camera.

The application supports direction, generation, Meta image generation, camera-to-shot saving, entity draft preview/apply/cancel, explicit accept/reject/repair, pause/resume, cancellation requests, and saved-run review. Replay disables mutation commands. Historical accounting without a saved ledger shows unknown values. Asynchronously failed commands produce a dismissible error banner.

## Checks run

- TypeScript `tsc --noEmit`: passed with Node 26.
- Vitest web suites: 40 tests passed across 4 files (33 terminal tests plus 7 viewport tests). Coverage includes pinned A / active B targeting, required rejection reasons, replay restrictions, duplicate dispatch, 1,000 incoming records with bounded 80-row history rendering, keyboard input protection, immutable prompt/package geometry lookup, entity preview/cancel, asynchronous failure visibility, unknown historical accounting, immutable pinned entity revisions, explicit uncertain-job retirement, fused-child edit restrictions, model-aware take labels/camera duration, preserving protected asset provenance, stale-source action guards, verified input attachment, positive scale, bounded light intensity, submitted/planning input separation, previous-model receipt navigation, attributed creative decisions, missing decision records, and concurrent disposition locks, ordered start/end frame roles, imported evidence labels, and exported derivative hashes.
- Production Vite build: passed. Root integration split the viewport: main bundle approximately 302 kB / 93 kB gzip; viewport approximately 658 kB / 165 kB gzip. Vite emitted its >500 kB viewport chunk advisory.
- Browser: actual room and Meta actor-image artifacts loaded; actual Producer and Director of Photography tasks exposed submitted application prompts, context manifests, and output artifacts. No browser errors were reported during the final review.
- Pause then resume executed through the UI and produced recorded backend events. The browser returned to live mode.
- An existing recorded run opened with the persistent replay label, one canvas, and disabled Generate control; return-to-live succeeded. This is saved-run inspection, not a fabricated timed reenactment.

## Geometry evidence

| CSS viewport | Center stage | Actual canvas | Inspector | Result |
|---|---:|---:|---:|---|
| 1440×900 | 832×422 | 830×354 | 320 px | No horizontal overflow; permanent output/activity/composer visible |
| 1920×1080 | 1232×582 | 1230×514 | 384 px | Wide layout visually reviewed |
| 1280×800 | 720×388 | 718×320 | 304 px | Exact canvas fit; all primary controls in bounds |

The laptop activity row is compacted to 68 px (header plus one fully visible event, scrollable history) to preserve an actual 320 px canvas after toolbar/footer/borders. Output shelf remains 160 px. The entity list scrolls independently so camera and crew sections stay visible.

Screenshots: `review-1440.png`, `review-1920.png`, `review-1280.png`, and `review-replay-1280.png`. The replay screenshot predates the final historical-budget display correction; automated coverage verifies that correction.

## Remaining verification boundaries

- Initial screenshots show missing actor assets. The later `review-actors-1440.png` captures both published textured actors seated in the room with one canvas and a momentary 60 fps / 190k-triangle reading. This is not a sustained performance measurement or proof of final realism/continuity.
- Sustained 60-second navigation p95, event commit-to-paint latency, and memory trend across five cycles remain unmeasured. A momentary viewport FPS label is not a substitute for those measurements.
- Handoff records emit `from_task_id`, `to_task_id`, `input_refs`, and `summary`. The UI also accepts the design contract's sender/receiver/artifact fields. With no explicit status, it displays `Inputs attached` only when a source output artifact ID is present in the destination's recorded input references; otherwise it displays `Consumption unrecorded`. It does not infer consumption from task completion.
- Layout panes are fixed responsive regions; free docking and pane resizing are not implemented.

## Explicit uncertain-submission retirement

Added a required-reason retirement dialog under the model-call inspector for `submission_unknown`. It names the exact job, explains possible continuing remote work, retains the full reservation, and sends only `retire_unknown {job_id, reason}`. No automatic replacement or retry is submitted. The regression test verifies the generation gate remains locked until a `dispatch_retired` snapshot arrives and held cost remains visible afterward.

The live Seedance reference attempt returned HTTP 400 `InputImageSensitiveContentDetected.PrivacyInformation`. The terminal displayed the actual command failure, saved application prompt, three ordered reference artifacts, and detailed provider receipt. The overview now prioritizes the selected job's state rather than inheriting the associated shot's draft status. No generation or retirement commands were issued during this browser review.

Entities with `state.included_in_entity_id` show their parent actor and disable independent transform/material controls. A link opens the actor's own inspector. This communicates the domain restriction for geometry fused into a composite asset.

## Integrated video review

The first completed fal Veo 3.1 take played locally to `ended=true` at exactly 8 seconds, decoded at 1280×720, and returned no media error. Clicking recorded continuity evidence sought the player to 7.85 seconds. Opening the video artifact paused shelf playback; both media elements were paused and the world retained one canvas. No take was accepted, repaired, or regenerated during this review.

The inspector displays four sampled-frame continuity passes and one explicit unknown for world-space camera orientation. Those recorded observations are limited to sampled frames; UI playback does not establish full continuity or photorealism. The take's source revision differs from the later composite-actor revision shown in the live world, and both source/head identifiers remain visible.

`review-video-1440.png`, `review-video-1920.png`, and `review-video-1280.png` show simultaneous world, completed video, Meta output, four production roles, and review controls. At all three widths, the eight actual model labels fit without truncation and neither the page nor model strip overflows horizontally. Secondary model-state text is compacted below 1700 px while the active video state stays visible; real status colors and explicit model receipts preserve inspection. The 1280×800 canvas remains 718×320 and its 304 px inspector stays in bounds.

## Final action and failure review

Accept and Repair are disabled when a take's source scene differs from the accepted head, with an explicit explanation. Reject remains available. An already-open acceptance dialog also becomes disabled if the head advances. `review-stale-take-1440.png` verifies the live restriction for the first historical take.

The second take, `take-d275fa9f116f40f3`, has a real recorded failure for the single-green-cup assertion. Its evidence action sought the selected video to exactly 4.00 seconds, and the linked evidence image visibly shows two green cups. `review-failure-observation-1440.png` captures the assertion alongside the player; `review-duplicate-cup-evidence.png` shows the original evidence artifact and its hash. The review did not submit acceptance, rejection, repair, or generation.

XYZ scale fields preview and apply finite positive multipliers. Mesh baseline dimensions remain read-only. Light entities additionally expose authored intensity from 0 to 1000; other state fields remain unavailable. Apply emits only creative state, while previews retain provenance. Tests verify rejection of zero/negative scale and out-of-range intensity, plus clean command payloads. Browser checks previewed cup scale and light intensity, then cancelled both; intensity returned to 150, scale to [1,1,1], and one canvas remained. `review-light-preview-1440.png` shows the local preview before cancellation.

After the Meshy binding was added, all nine model labels remained untruncated at 1440 and 1280 pixels with no horizontal overflow. The latter is captured in `review-nine-models-1280.png`. No uncaught browser errors were reported; the isolated review browser was closed and production servers were left running.

## First-frame model and compact registry

The tenth binding introduced label truncation at 1280 px. Below 1400 px, the three explicitly inactive video bindings now move to an `Other models` inspector, whose rows open their actual latest receipts. The five Meta bindings, Meshy, and the active first-frame video binding remain fully readable. No provider picker or dispatch behavior was added.

The live first-frame package `package-7d2895d7054144a6` records one `reference_ids` entry and three `planning_reference_ids` entries. The inspector labels the former `Submitted video inputs · 1 reference`; the latter appears in a collapsed `Planner context · 3 references` section. Selecting the planner task still shows its own three recorded task inputs. The separate context never appears as additional video attachments.

The first accepted take was also inspected live: its creative decision displays the recorded build-system actor and complete bounded acceptance reason. The wardrobe failure and unknown camera-axis observation remain in the continuity section. Accept and Reject are both disabled after either disposition; a dialog opened before another operator decides is locked on the next snapshot. Decision metadata is explicitly unavailable when its event is absent.

With ten models, secondary state text is compacted through 2099 px to keep all names readable at 1440 and 1920. The active video state remains visible. The historical-model inspector is used below 1400.


## Endpoint conditioning and portable historical evidence

For `conditioning_mode=first_last_frame`, the two actual `reference_ids` are labeled Start frame and Proposed end frame in order. The end frame is explicitly described as guidance, not accepted scene state. Planner-only actor references remain in the separate collapsed context section. Tests verify the 2 submitted / 3 planner fixture without dispatching commands.

Imported tasks and handoffs show historical source attribution, source run and original task status. `demo.disposition_imported` is rendered as a Historical creative decision, distinct from locally executed acceptance/rejection. A missing local provider call is not rendered as a usable receipt. Prompt/artifact transformations other than `byte-identical` use Exported derivative hash, and prompt dialogs say Exported prompt rather than Full request. Tests verify these boundaries and make no provider calls.

The isolated portable demo was reviewed at port 8001 after a real CLI origin bug was fixed by the runtime owner: cross-origin-tagged built JS/CSS were rejected on the custom port, while same-origin fetches succeeded. The CLI now explicitly permits its configured loopback port. The frontend displayed imported Producer prompts and outputs, original source-run status, two historical handoffs (one Inputs attached and one Consumption unrecorded), and the rejected duplicate-cup take's historical actor/reason. The take's local video decoded at 8 s with readyState 4. No uncaught browser errors or commands were recorded during this review; the isolated browser was closed. Proof: `review-imported-task-1440.png`, `review-imported-decision-1440.png`.

With the eleventh video binding, inactive video history moves to Other models below 1700 px. Browser measurements confirm all 11 model names are untruncated at 1920, and all 7 current names plus Other models 4 fit at 1440 and 1280. There is no horizontal overflow; the 1280 canvas remains 718×320.


## Accepted first and second shot playback

The accepted first take `take-6880370771c44203` and second take `take-a28f62cd70744615` each played from 0 to 8 seconds in the production browser, ended normally at 1280×720, and reported no media error. Evidence buttons sought exactly to 7.85 s for shot 01 and 4.00/7.85 s for shot 02, leaving playback paused. Package/source/shot IDs, requested job model and job output artifact IDs match. The second take's typed patch adds null optional fields but semantically matches its immutable package's cup translation and `coaster: far` state. Current scene remained `scene-1a3969ad79504e53`, cup position [2.5, 0.49, 0.766].

The actual second package presents two endpoint video inputs and four planner references, while first-shot history preserves one video input and three planner references. Recorded review notes retain wardrobe, offscreen identity/seating and contact-geometry limits. The browser visibly reported a stale disconnected snapshot following a runtime restart; the explicit Reconnect action restored LIVE and current records without losing inspection state. No generation, disposition, or scene mutation commands were used; one world canvas remained and no uncaught browser errors were reported.


## Final three-shot state and capture

Final accepted take IDs are `take-6880370771c44203` (shot 01), `take-a28f62cd70744615` (shot 02), and `take-beb4d03c19fd4318` (shot 03). Final current scene is `scene-7af320dd22464014`. Shot 03 played fully from 0 to 8 seconds at 1280×720, ended normally, and reported no media error. All three shots show Accepted; acceptance is attributed to build-system with bounded review notes, rather than inferred from supervisor grades. Source and current-head IDs remain distinct and visible.

Final actual application captures: `review-final-sequence-1920.png`, `review-final-sequence-1440.png`, `review-final-sequence-1280.png`. The wide capture keeps the current scene's two actors and transferred far-coaster cup visible while pinning the selected final take's acceptance rationale. The 1920 image was copied to the user deliverable `outputs/ghost-director-terminal.png`. At 1280 the world canvas remains 718×320 and inspector 304 px, seven current model names plus Other models 4 remain untruncated, and no horizontal page overflow occurs.

An actual browser walkthrough is saved as `outputs/ghost-director-terminal-walkthrough.mp4`: H.264, 1440×900, 24 fps, no audio track. It records real selection and complete eight-second playback of each accepted take with corresponding world and recorded decision. It uses no simulated cursor or fabricated telemetry. All three playback passes ended at 8.0 s with no error. A decoded frame at 10 seconds was visually inspected (`review-recording-frame.png`).

Final verification: TypeScript no-emit check passed; all 40 web tests passed; production Vite build passed (302.06 kB main / 93.09 kB gzip; 657.78 kB viewport / 164.91 kB gzip). The viewport chunk advisory remains. Source commit is pending the lead's final repository commit. No uncaught browser errors were reported; both isolated review browser sessions were closed, and production servers were left running. No generation, acceptance/rejection/repair, or scene mutation commands were issued during the final review. Sustained navigation p95 and long-session memory tests remain outside this verification.


### Durable decision attribution follow-up

After activity advanced beyond the original event window, shot 01 temporarily showed attribution unavailable. The backend now retains original native and imported decision events alongside the bounded activity tail. A fresh production browser verified that shot 01 again shows its original build-system actor and complete bounded acceptance reason while preserving the wardrobe failure. Proof: `review-durable-decision-1440.png`. The actual walkthrough was replaced after this fix: all three takes display their recorded attribution and play fully to 8.0 s without media errors. No uncaught browser errors occurred, the review browser was closed, and scene/provider state was unchanged. UI source is frozen for the lead's commit.

Disposed accepted/rejected takes no longer show the stale-source action warning: a child revision after acceptance is expected. Immutable source/head IDs and disabled disposition controls remain. Existing accepted/rejected regression cases assert the warning is absent; undecided stale-source cases retain the guard.

### Production-designer rebuild — 2026-11-03

The workspace now exposes Scene, Materials, Lighting, and Camera tools beside a persistent world canvas. Build set / Compose shots / Review takes organize the operator's work without remounting the renderer. Room geometry is an unapplied draft until Apply design rebuilds the canonical asset; surface shading and exposure preview locally. Named surface color/finish controls describe texture tint accurately. Window add/remove honors the domain maximum of four and inserts only into a valid free wall span. Existing material textures are preserved, not replaced by invented material families.

Create shot runs the Meta planning workflow and selects its exact completed shot receipt. Save view as shot also selects the new shot from the backend's direct ShotPlan result. Generate video explicitly targets the selected saved shot, is disabled for a mismatched selected camera or unsaved edits, and stays locked through preparation. Local scene/camera/light edits remain possible during generation against frozen inputs. Unrelated room drafts survive camera/light commits; conflicting saved changes produce an explicit notice. Invalid numeric form values never reach the viewport. Local rendering is a distinct action, with saved source revision, camera, quality, and stale-image status. Render evidence chooses the newest image timestamp and excludes JSON receipts. Undo uses the durable sequential undo target. Old failures remain inspectable without reappearing as fresh alerts on load.

Real browser verification used isolated production API8003 only. Material tint preview and Discard draft were compared visually. Apply design completed, then Undo restored the saved wall color #9daeb8 in scene-0869f8b4989c442d. Apply camera changed the existing GDR-DP-CAM-001 to35mm, producing scene-61110f5d8dfb4c0f. Saving Designer camera study created shot-12ae236ac5724c37 and automatically selected its fifth shot card. Draft still completed from scene-b77ac0f17f0343de and camera-64370f3a625b481d, producing image art-747f88d4-fc7d-4a38-abd5-bd6c7437fa08. All of those actions created zero provider jobs. The live API8000 production was not mutated.

Actual keyboard deletion of focal length left an empty invalid field, displayed its validation notice, disabled Apply camera, and retained one functioning canvas; Reset lens restored the saved view. No uncaught browser errors were reported. At1280×800: page scroll width1280 (no horizontal overflow), world canvas798×252, inspector284px, five shot cards present with scrolling available. Screenshots saved under workspace work/: production-designer-1600.png and production-designer-1920.png; both are actual application renders with real set, media, crew and historical evidence. The automation browser was closed after verification.

Final frontend verification: TypeScript no-emit check passed;63 web tests passed (51 terminal/state,12 viewport); Vite production build passed. Main bundle330.03kB /101.56kB gzip; lazy viewport660.80kB /165.99kB gzip. The existing viewport chunk-size advisory remains. Browser checks are task-focused; long-session memory/performance profiling and subjective final-render realism are outside this frontend verification. Source is ready for the lead's final review and commit.

### Model presentation update

Non-Meta bindings use capability labels across the model desk (including tooltips), video headings/accessibility labels, budget rows, history, service receipts and artifact previews. Genuine Meta SAM 3D names remain even for hosted execution. Read-only payload aliases are marked as display previews; original-file access and raw editable prompt values remain intact. The existing routing/duration and immutable-prompt tests pass with the new labels; negative checks cover rendered text and title/aria/alt attributes. All63 frontend tests and TypeScript/production build pass. The lazy viewport chunk-size advisory is unchanged. No backend configuration, production record or provider call changed.

## Reference-first creative workspace · 2026-11-03

The world now shares the main work area with recorded creative task cards, explicit handoff edges, prompt/input links, and output artifacts. The latest active run is followed automatically; recorded runs remain selectable. No agent nodes, completion states, progress percentages, or generated results are synthesized for display. The reference workspace adds category/identity/version navigation, exact-image inspection, original-sheet inspection, required reference slots, raw image upload, generation, and explicit per-version decisions. Approved character/prop reconstruction exposes authored physical height and orientation; review is an ephemeral view on the same world canvas, separate from saved placement. The shared shot planner uses the same canvas for transient blocking playback and selects the new immutable shot when its save receipt arrives.

Verification performed:

- `npm test -- --reporter=dot`: 75 tests passed across 9 files. New tests cover real active-run following, output arrival, exact-version approval, incomplete/replay gating, historical read-only versions, stale upload failure, original sheet versus crop inspection, approved-revision reconstruction while a newer draft exists, placement degree/radian conversion, and saved-plan receipt selection. Upload and command responses in these tests are mocked boundary responses; they do not establish live provider quality.
- `npm run build`: TypeScript and Vite passed. Main JavaScript: 367.42 kB (111.59 kB gzip); existing lazy viewport: 660.80 kB (165.99 kB gzip). Existing viewport chunk-size advisory remains.
- Actual localhost browser at 1280×800, 1600×1000, and 1920×1080: one WebGL canvas, zero horizontal document overflow, creative crew content fits its panel. At 1280×800 the command composer ended at y=791; at 1920×1080 it ended at y=1064.
- Actual browser switched References → Compose shots → Build set, retaining one canvas. Blocking playback advanced to 3.32 seconds with an explicit ephemeral-preview label; exiting the mode removed that label. No production command or paid provider call was submitted during this browser review.
- Visible text at 1600 and 1920 contained no non-Meta model names. Recorded provider identities remain in original artifacts and source metadata.
- Browser review used the existing live production read-only. Its reference registry was empty at review time, so populated reference images, live uploads, and provider reconstruction still require the isolated end-to-end validation recorded elsewhere.


### Populated-reference and actual-video follow-up

The isolated runtime at port 8004 was then inspected using actual provider artifacts. The approved amber-bottle image, Meta SAM 3D reconstruction, normalized model and original receipts were visible. Reconstruction inspection temporarily added an explicitly unplaced preview to the existing canvas and disabled scene writes until exit. The technical Blender clip decoded at 640×360, 4.0 seconds, readyState 4. The real generated take `take-b25217869f1c45ca` decoded at 854×480, 4.041667 seconds, readyState 4 and played to its end through the native video control. Programmatic autoplay was correctly blocked by Chrome until a user gesture. No creative take acceptance was recorded.

The authorized local crop evaluation saved Mara's back at normalized [1/3, 0, .639, 1] (587×1280) and face at [.645, 0, 1, 1] (682×1280). Both preserve original artifact `art-a274b50e-574d-4fb3-b9a4-cf3e38269df2`; final version 7 remains draft. An initial CLI label-lookup failure recorded an unchanged default crop as version 5; this harmless history is retained. Fresh element references and value readback confirmed the corrected v6/v7 inputs before save. No paid browser request was made.

A focus bug observed during concurrent background asset creation was fixed: new assets from another session no longer steal the operator's selected reference. Crop editing now uses the original sheet, a visible selection overlay, four percentage bounds and persisted crop provenance. Location guidance binding and explicit actor/prop replacement are available with truthful scope labels.

Hidden-world optimization was measured in the real browser: submitted frame counter 787 remained 787 while References hid the world for 2.4 seconds, render state suspended; returning to Build set resumed rendering to counter 898. The same single canvas/context remained mounted throughout.

Final checks: 90 web tests across 10 files passed; TypeScript and Vite passed. Final bundles: main 372.69 kB / 112.99 kB gzip; viewport 661.11 kB / 166.09 kB gzip. The existing viewport-size advisory remains. Workspace output screenshots and `ghost-director-workspace-verification.json` record the observed evidence and limitations.
