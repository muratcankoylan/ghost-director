# Ghost Director: Meta hackathon preparation

Status observed **November 3, 2026, America/Toronto**. The participant reports being accepted with participant access and has selected the **Indie developer track**. This is user-reported registration status. No project submission, finalist selection, award or endorsement is claimed here.

## Verified public event information

The [official Meta Global AI Developer Hackathon page](https://dev.meta.ai/events/global-hackathon) lists the Indie developer track for individual builders and students, evaluated for creativity, technical ambition and working execution. It lists project submissions due **November 4, 2026 at 9:00 PM Eastern** and identifies two themes: *Build for Your World* and *Push the Frontier*. Dates may change on that page.

The [organizer event page](https://cerebralvalley.ai/e/meta-ai-developer-hackathon) independently lists the same submission time and track. Both pages state that selected participants must accept the Official Rules. The linked public [application overview and waiver](https://cerebralvalley.ai/e/meta-ai-developer-hackathon/waiver) returned only a loading page during this read-only check. Consequently, the complete participant rules and authenticated submission-form requirements have **not** been verified by this document. Check those inside the participant portal before submitting.

## Project positioning

Ghost Director brings reference-based preproduction into an inspectable web workspace. Approved photographs define appearance; the navigable 3D set supports planning; a minimal synchronized Blender scene supplies camera and blocking guidance. One structured shot plan connects the motion render with the final-video request.

The proposed fit is *Build for Your World*: help directors inspect assets, arrange a scene and understand exactly which approved inputs will reach a generation request. This is the project's positioning, not a track or theme assignment by the organizers.

Meta is central to the implemented workflow:

- Meta Image supplies original photographic references.
- Meta SAM 3D Objects produces textured meshes from approved images through a hosted service.
- Existing bounded Meta reasoning and visual-review roles expose application prompts, outputs and recorded handoffs where those roles have actually run.

Local Blender and Three.js handle deterministic scene/motion operations. Other video adapters are separately attributed in technical provenance; the application must not imply that Meta generated an output produced by another service. Using hosted Meta APIs also does not imply that all model weights or services are open source.

## Demonstration evidence and gaps

The [fresh Conservator experiment](FRESH-EXPERIMENT.md) records three new photographs and two actual image-to-3D results, with $0.07 in estimated application-ledger charges. At its dated observation, photographs were approved for the test, both meshes still awaited comparison/placement review, and this example had no motion clip, submission package or movie take.

The public demonstration should show completed steps and expose unfinished ones. A suggested walkthrough is:

1. Inspect original approved photographs and persistent identity/version records.
2. Compare a returned mesh against those photographs and show any recorded discrepancy.
3. Inspect the authored room and its explicit location-reference binding.
4. Show actual Blender task state, a real motion output when available, and its matching shared shot plan.
5. Inspect the exact appearance images, motion clip, compiled prompt and cost estimate before dispatch approval.

These are demonstration suggestions, not verified organizer submission requirements. Do not substitute a simulated progress animation for an actual job, present an unreviewed mesh as approved, or describe an ungenerated film as completed.

## Run and review

Use the [installation and local testing guide](../README.md#quick-start). Source installation, an empty procedural set and local Blender operations are distinct from paid media generation. Keep credentials in ignored local configuration, and leave live calls disabled until separately authorized.

Application code licensing is scoped in [LICENSE-GHOST-DIRECTOR.md](../LICENSE-GHOST-DIRECTOR.md). Review [release boundaries](PROVENANCE.md) before distributing generated media or inherited Studio OS material. Public repository publication is not a hackathon submission, and this document does not claim that a live hosted demo or a portable generated-media bundle has been published.
