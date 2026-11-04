# Ghost Director — Reference-First World Design and Video Production

## Architecture Update for Codex

**Status:** Proposed correction to the existing Ghost Director architecture  
**Objective:** Preserve the existing navigable 3D environment while making approved photorealistic references—not 3D renders—the authoritative visual source for final video generation.

## 1. Core Principle: Build the World From References, Not References From the World

Ghost Director must follow a **reference-first** production pipeline.

The system should not create a generic 3D scene and then derive the characters, location, props, colors, lighting, or final video appearance from screenshots of that scene. Instead, the director and production team must first establish and approve photorealistic visual references, for example using Nano Banana Pro or Seedream.

Those approved images define the intended visual identities of the characters, environment, props, set dressing, materials, and lighting. They are the **visual source of truth**.

**Correct direction:**

Director's brief → Approved photorealistic references → Reference-matched 3D assets → Navigable production set.

**Incorrect direction:**

Generic clay 3D world → Screenshots/renders → Visual references for the final film.

## 2. Preserve the Existing 3D World; Improve Its Assets

Ghost Director has already demonstrated that it can create a basic, navigable 3D environment. Therefore, the primary engineering priority is **not to rebuild the world engine from scratch**.

The priority is to establish a **Reference-to-3D Asset Pipeline** that can:

1. Take approved images of characters, locations, furniture, props, and important set elements.
2. Generate a corresponding 3D asset, retrieve a suitable existing asset, or adapt an existing asset to resemble the reference as closely as practical.
3. Compare its geometry, proportions, materials, colors, and recognizable visual identity against the approved image(s).
4. Place the asset into the existing navigable Ghost Director set while preserving its identity and spatial relationships.
5. Keep the asset linked to its original approved references in the Asset Registry.

The closer the interactive 3D set looks to the approved references, **the more valuable it becomes**. Visual fidelity is not optional in the product vision: the director should be able to walk through a realistic set and meaningfully inspect the characters, props, lighting design, materials, and composition that were approved during preproduction.

However, high visual fidelity in the 3D set does **not** make that set the source of the final video's appearance. That distinction must remain explicit.

## 3. Visual Reference Standards

Before building final 3D assets, create and approve reference sets with consistent asset IDs.

### Characters

For each principal character, produce at least:

- **Front full-body view:** neutral pose, clearly showing face, body proportions, clothing, shoes, and accessories.
- **Back full-body view:** the same person and outfit, with matching body proportions, hair, and garment details.
- **Face close-up:** the same identity, with clear facial anatomy and distinguishing details.

Do not treat these as three independently designed characters. Use the established character identity as an input to every additional view. Record the approved images under a persistent identifier such as `CHAR_001`.

### Locations

Start with **one master location image** defining the approved design. Additional angles may be generated when required, but they must be derived from and remain consistent with the master reference and a shared spatial plan. Avoid independently inventing incompatible architecture or furniture placements in each view.

Store the location under an identifier such as `LOC_001`, including essential layout and material information.

### Props and Furniture

Create product-style reference images for important individual objects, adding extra angles only when useful. Objects whose appearance changes during a scene—such as food being eaten—should retain one asset identity with separate approved visual states as needed (e.g., `PROP_003`, untouched / partially eaten).

## 4. Two Synchronized Representations of One Set

After references have been approved, Ghost Director should maintain **two representations of the same scene**, both driven by shared `Asset Registry` and `Scene State` records.

### A. Realistic, Real-Time 3D Set — The Director's Working Environment

This is the interactive, navigable world. Its purpose is to let the director and relevant production departments:

- Walk through the location and understand the actual spatial arrangement.
- Inspect approved characters, props, furniture, materials, colors, and lighting through their 3D representations.
- Move or adjust set elements, blocking positions, and camera placements.
- Give instructions to the Director of Photography, Production Designer, Set Decorator, and other relevant departments.
- See the results of those changes live in the set before committing to a shot.

This world **should be as faithful as possible to the approved photorealistic references**. When a reference-derived 3D asset is not accurate enough, the difference should be visible and actionable rather than silently accepted.

### B. Minimal Blender Scene — The Technical Motion Environment

The Blender version represents the **same identities, coordinates, scale, orientations, blocking, and scene relationships** using the simplest geometry needed for motion planning.

It does **not** need photorealistic character models, elaborate prop textures, or realistic materials. Avoid unnecessary detail that might introduce conflicting visual information into the motion reference.

Blender's job is to produce accurate technical guidance for:

- Camera paths, framing, lens settings, and movement timing.
- Character blocking, facing directions, poses, and basic actions.
- Prop locations and character-object interactions.
- Shot duration, action timing, and spatial continuity.

**The two representations should be created or updated from the same approved scene data and remain synchronized.** Blender is not the stage from which the realistic world is visually derived, and the realistic set is not converted into a detailed Blender replica.

## 5. The Source of the Final Seedance Video

This is a strict separation of responsibilities.

| Component | Responsibility |
| --- | --- |
| **Approved original image references** | Authoritative source for the final film's character identities, costumes, location appearance, props, materials, and lighting aesthetic. |
| **Realistic real-time 3D world** | Navigable, interactive production set used to inspect, design, arrange, and control the scene. It should closely match the references, but is **not** the authoritative visual input for the final video. |
| **Minimal Blender reference video** | Source of camera movement, framing, blocking, action timing, and spatial/motion guidance. |
| **AI Shot Director + Prompt Compiler** | Translate the approved shot plan into Seedance-ready instructions and attach the correct original references and Blender video. |

**Do not automatically use screenshots, still frames, image-to-video starting images, or renders from the real-time 3D world as substitutes for approved photorealistic reference photographs.**

The final Seedance output should be directed by the **original approved image references** for appearance and by the **Blender clay motion video** for movement and blocking.

The goal is not to extract a video from the rendered 3D world. The 3D world is a powerful, visually faithful planning and control interface; the original images remain the visual source for the video model.

## 6. One Shot Plan, Two Connected Prompt/Execution Tasks

When the director is satisfied with the set arrangement, they describe the desired shot—what should happen, from which camera position, and at what moment.

A single structured **Shot Plan** should capture:

- Shot ID and scene ID.
- Relevant character, location, and prop IDs.
- Camera position, movement path, framing, lens, and duration.
- Character blocking and action timeline.
- Approved visual references and constraints.

The AI system then performs two linked tasks using **the same Shot Plan**, not two independently invented creative directions:

**Task 1 — Blender Control:** Translate the Shot Plan into Blender MCP actions. Update the minimal technical scene, animate the camera and basic character/object movement, and render a clay reference video. Export structured camera and timing data as well.

**Task 2 — AI Shot Director / Seedance Prompt Engine:** Read the approved Shot Plan, the actual Blender camera/blocking data, the clay video, and the approved original images. Write a cinematic Seedance prompt that describes the *existing* shot accurately. Do not invent extra camera movements, cuts, character actions, props, or appearances.

A **Prompt Compiler** should resolve asset IDs into the correct approved image files, attach the matching Blender reference video, apply the video API's supported reference-label format, and present the complete production request for director approval.

This can initially be implemented using one LLM with distinct responsibilities. A full multi-agent Studio OS deployment is not a prerequisite for the first prototype.

## 7. Example Director Workflow

1. The director asks for an elegant dining room with four specific characters, food, furniture, and lighting.
2. The system creates and presents character, location, and prop image references for approval.
3. Approved references are recorded. Matching 3D assets are created or adapted and installed in the existing navigable world.
4. The director walks through that realistic environment and asks the Set Decorator to reposition a table or the DP to adjust the lighting and camera.
5. Those changes are recorded in the shared Scene State and reflected in the simple Blender scene.
6. The director specifies a 15-second shot with exact movement and action timing.
7. The Shot Plan drives Blender MCP, producing the minimal clay camera/blocking video.
8. The AI Shot Director writes a Seedance-specific prompt based on the same plan; the Prompt Compiler attaches **approved original photos**, **not real-time-world screenshots**, plus the **Blender clay motion reference**.
9. The director checks the prompt and references, authorizes generation, and reviews the resulting cinematic video.

The team has already observed useful adherence to Blender-based motion references in manual Seedance tests. The next goal is to **productize that manual procedure** as a repeatable, controllable workflow within Ghost Director.

## 8. Studio OS and Future Continuity Reasoning

In the longer-term architecture, Studio OS specialist agents and multimodal LLMs should use the Asset Registry, Scene State, Shot Plan, and verified video results to support continuity: character identity, props, meal progression, camera geography, and between-shot state changes.

For the initial prototype, prioritize the reference-first asset pipeline, realistic set experience, Blender synchronization, shot planning, and Seedance prompt preparation. Keep the data structures compatible with future agent-driven reasoning without making full autonomous continuity management a dependency today.

## 9. Implementation Instruction for Codex

**Update the existing Ghost Director application rather than rebuilding it.** The current system already has a basic navigable 3D world; investigate and reuse those capabilities.

The primary changes are:

- Make **approved photorealistic references** the starting point for characters, locations, props, materials, and visual design.
- Prioritize **reference-matched 3D asset generation/adaptation** inside the existing real-time world.
- Keep the realistic world visually faithful, interactive, and navigable—but never confuse its renders with the authoritative reference photographs for final video generation.
- Maintain a **minimal, synchronized Blender representation** solely for technical camera, blocking, and action motion references.
- Use **one authoritative Shot Plan** for Blender MCP execution and Seedance prompt generation.
- Send Seedance a controlled package consisting of **approved original image references + Blender clay reference video + a shot-accurate prompt**, subject to the actual API's supported inputs.
- Preserve director approval and control throughout the pipeline.

**Final architectural rule:**

> **The approved photographs define what the film should look like. The realistic 3D world lets the director walk through, inspect, design, and control that world. Blender defines how the camera and scene move. The AI prompt system brings those sources together for Seedance without replacing the original visual references with screenshots of the 3D set.**

**We are not generating the visual references from the world. We are building the world from the visual references.**
