"""Reference-first shot compilation. Frozen plans, never independent motion prompts."""
from __future__ import annotations

import hashlib
import json
from typing import Literal
from pydantic import Field, field_validator
from ghost_director.domain.models import Contract, MotionPlan, ReferencePin, ShotPlan, apply_entity_patches, new_id, now_iso
from ghost_director.jobs.store import Conflict
from ghost_director.providers.reference_video import REFERENCE_VIDEO_MODEL, compile_reference_video


class PackageApproval(Contract):
    accepted: Literal[True]
    review_sha256: str = Field(pattern=r'^[a-f0-9]{64}$')
    actor: Literal['local-operator']
    note: str = Field(min_length=1, max_length=2000)

    @field_validator('accepted', mode='before')
    @classmethod
    def explicit_acceptance(cls, value):
        if value is not True:
            raise ValueError('Explicit director authorization is required')
        return value

    @field_validator('note', mode='before')
    @classmethod
    def trim_note(cls, value):
        return value.strip() if isinstance(value, str) else value


class GeneratePackageRequest(Contract):
    package_id: str
    approval: PackageApproval


class UpdateShotRequest(Contract):
    shot_id: str
    name: str | None = Field(default=None, min_length=1, max_length=120)
    action: str | None = Field(default=None, min_length=1, max_length=12000)
    duration: int | None = Field(default=None, ge=4, le=30)
    motion: MotionPlan | None = None
    reference_pins: list[ReferencePin] | None = Field(default=None, max_length=30)


def plan_digest(shot: ShotPlan) -> str:
    return hashlib.sha256(shot.model_dump_json().encode()).hexdigest()


def package_review_digest(package: dict) -> str:
    """Bind director authorization to every immutable field shown for dispatch."""
    data = {key: value for key, value in package.items() if key != 'review_sha256'}
    return hashlib.sha256(json.dumps(data, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()).hexdigest()


def revise_shot(store, scene, payload):
    from ghost_director.motion import validate_motion
    from ghost_director.references import ReferenceRegistry
    request = UpdateShotRequest.model_validate(payload)
    source = ShotPlan.model_validate(store.get_record('shots', request.shot_id))
    values = source.model_dump(mode='json')
    values.update(request.model_dump(mode='json', exclude_none=True, exclude={'shot_id'}))
    values.update(id=new_id('shot'), parent_id=source.id, scene_revision_id=scene.id, created_at=now_iso(), status='draft')
    shot = ShotPlan.model_validate(values)
    if shot.camera_id not in {camera.id for camera in scene.cameras}:
        raise ValueError('Shot camera is absent from the current scene')
    if shot.reference_pins:
        ReferenceRegistry(store).approved_references([pin.model_dump() for pin in shot.reference_pins])
    if shot.motion:
        validate_motion(scene, shot)
        # End-state evidence must derive from the exact executable motion, not a
        # second independently authored patch left over from an older plan.
        from ghost_director.domain.models import EntityPatch
        shot.proposed_patch = [EntityPatch(entity_id=track.entity_id, position=track.keyframes[-1].position,
                                          rotation=track.keyframes[-1].rotation) for track in shot.motion.tracks]
    apply_entity_patches(scene, shot.proposed_patch, revision_id='validation')
    store.upsert_record('shots', shot.model_dump(mode='json'), event_type='shot.plan_revised')
    return {'shot_id': shot.id, 'parent_id': source.id}


async def compile_package(store, scene, shot, preview, *, run_id):
    from ghost_director.identity_bindings import bind_reference_identities
    from ghost_director.motion import validate_motion
    from ghost_director.references import ReferenceRegistry
    if shot.scene_revision_id != scene.id:
        raise Conflict('Shot plan belongs to an older scene; review and save a new plan')
    validate_motion(scene, shot)
    if preview['shot_id'] != shot.id or preview['scene_revision_id'] != scene.id or preview['plan_sha256'] != plan_digest(shot):
        raise Conflict('Motion preview does not match this exact shot plan')
    pins = [pin.model_dump(mode='json') for pin in shot.reference_pins]
    if not pins:
        raise ValueError('Select approved identity references before compiling')
    references = ReferenceRegistry(store).approved_references(pins)
    if len(references) > 30:
        raise ValueError('This conditioning route supports at most 30 images')
    video = store.artifact(preview['video_id'])
    if video['mime_type'] != 'video/mp4':
        raise ValueError('Motion reference is not an MP4')
    receipt_artifact = store.artifact(preview['receipt_id'])
    receipt_bytes = store.artifact_path(preview['receipt_id']).read_bytes()
    if hashlib.sha256(receipt_bytes).hexdigest() != receipt_artifact['sha256']:
        raise Conflict('Motion receipt no longer matches its recorded hash')
    receipt = json.loads(receipt_bytes)
    if receipt.get('shot_id') != shot.id or receipt.get('scene_revision_id') != scene.id or receipt.get('video_sha256') != video['sha256'] or not receipt.get('evaluation', {}).get('passed'):
        raise Conflict('Motion receipt has not verified this exact shot and video')
    if receipt.get('fps') != shot.fps or receipt.get('frame_count') != shot.duration * shot.fps or receipt.get('duration_seconds') != shot.duration:
        raise Conflict('Motion receipt timeline differs from the shared shot duration and frame rate')
    expected_input = {'scene': scene.model_dump(mode='json'), 'shot': shot.model_dump(mode='json'), 'motion': shot.motion.model_dump(mode='json')}
    expected_hash = hashlib.sha256(json.dumps(expected_input, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()).hexdigest()
    if receipt.get('source_sha256') != expected_hash:
        raise Conflict('Motion receipt was produced from a different scene, shot, or identity binding')
    bindings, coverage_warnings = bind_reference_identities(scene, references, receipt)
    # Artifact bytes are independently verified before any external request.
    for artifact_id in [video['id'], *[entry['artifact_id'] for entry in references]]:
        artifact = store.artifact(artifact_id)
        if hashlib.sha256(store.artifact_path(artifact_id).read_bytes()).hexdigest() != artifact['sha256']:
            raise ValueError('A submitted reference failed its content hash')
    mapping = [{'tag': f'@Image{i + 1}', 'asset_id': entry['asset_id'], 'revision_id': entry['revision_id'],
                'slot': entry['slot'], 'name': entry['name'], 'artifact_id': entry['artifact_id']} for i, entry in enumerate(references)]
    prompt = '\n'.join([
        'Produce one continuous photographic live-action movie shot. No cuts or invented additions.',
        f'Duration: {shot.duration} seconds. Aspect ratio: 16:9. Director instruction: {shot.action}',
        'APPEARANCE AUTHORITY: approved image references. Preserve identities, clothing, architecture and prop appearance.',
        *[f"{item['tag']}: {item['asset_id']} / {item['name']} / {item['slot']} / revision {item['revision_id']}" for item in mapping],
        'MOTION AUTHORITY: @Video1 is the technical Blender motion reference for this exact shared plan.',
        'IDENTITY TO PROXY MAP: Each entity below names the exact colored proxy in @Video1 and the approved images that define its appearance. Technical RGBA colors identify proxies only; never copy those colors to final appearance.',
        json.dumps(bindings, separators=(',', ':')),
        'Follow its camera, root blocking, physical placement and timing. Do not reproduce proxy colors, clay materials or simplified geometry.',
        'APPEARANCE COVERAGE: '+('All visible set entities have approved reference bindings.' if not coverage_warnings else 'The following authored set elements have no approved appearance images. Preserve their layout; their visual finish is unspecified and must be reviewed: '+json.dumps(coverage_warnings, separators=(',', ':'))),
        'The technical footage supports root translation and rotation only. Articulated acting is a generation instruction, not measured rig motion.',
        'CAMERA AND BLOCKING TIMELINE (meters, Z-up, seconds):',
        json.dumps(shot.motion.model_dump(mode='json'), separators=(',', ':')),
        'CONTINUITY CONSTRAINTS:', *shot.assertions,
    ])
    if len(prompt) > 12000:
        raise ValueError('Compiled prompt exceeds the validated 12000-character limit; simplify the plan or reduce reference coverage')
    prompt = store.redactor.text(prompt)
    # Run the same actual-media boundary used by dispatch before announcing that
    # the request is ready. Small crops and undecodable clips need local repair,
    # not a surprise after the director authorizes a billable attempt.
    _, pricing = await compile_reference_video(store, prompt, [entry['artifact_id'] for entry in references],
        [video['id']], duration=shot.duration, resolution='480p')
    if abs(pricing['input_video_seconds'] - shot.duration) > 1 / shot.fps:
        raise Conflict('Decoded motion clip duration differs from the shared shot')
    prompt_art = store.publish_artifact(prompt, kind='prompt', mime_type='text/plain', metadata={'name': 'Compiled cinematic instructions', 'shot_id': shot.id, 'compiler': 'reference-motion-v1'}, run_id=run_id)
    package = {'id': new_id('package'), 'workflow': 'reference-motion-v1', 'run_id': run_id,
               'shot_id': shot.id, 'scene_revision_id': scene.id, 'camera_id': shot.camera_id,
               'shot_plan': shot.model_dump(mode='json'), 'plan_sha256': plan_digest(shot),
               'prompt': prompt, 'prompt_ref': prompt_art['id'], 'model': REFERENCE_VIDEO_MODEL,
               'conditioning_mode': 'images_and_motion_video', 'duration': shot.duration, 'resolution': '480p',
               'reference_ids': [entry['artifact_id'] for entry in references], 'reference_pins': pins,
               'reference_mapping': mapping, 'motion_video_id': video['id'], 'motion_receipt_id': preview['receipt_id'],
               'entity_reference_bindings': bindings, 'appearance_coverage_warnings': coverage_warnings,
               'assertions': shot.assertions, 'proposed_patch': [p.model_dump(mode='json', exclude_none=True) for p in shot.proposed_patch],
               'created_at': now_iso(), 'pricing_estimate': pricing,
               'limitations': ['Root motion only; no articulated performance guarantee.', 'Reference fidelity and generated motion require output review.']}
    package = store.redactor.value(package)
    package['review_sha256'] = package_review_digest(package)
    store.upsert_record('packages', package, run_id=run_id)
    return package


async def validate_package_dispatch(store, scene, package, approval):
    """Recheck the exact reviewed package without publishing or calling a provider."""
    from ghost_director.references import ReferenceRegistry
    if package.get('workflow') != 'reference-motion-v1':
        raise ValueError('Use the legacy shot workflow for this package')
    if package.get('scene_revision_id') != scene.id:
        raise Conflict('Package scene has changed; compile from the current reviewed plan')
    digest = package_review_digest(package)
    if package.get('review_sha256') != digest or approval.review_sha256 != digest:
        raise Conflict('Director approval must match this exact reviewed package; prepare and review a new submission')
    shot = ShotPlan.model_validate(package['shot_plan'])
    if plan_digest(shot) != package['plan_sha256'] or shot.id != package['shot_id'] or shot.scene_revision_id != scene.id:
        raise Conflict('Package no longer matches its immutable shot plan')
    from ghost_director.motion import validate_motion
    validate_motion(scene, shot)
    if package['duration'] != shot.duration or package['model'] != REFERENCE_VIDEO_MODEL or package['conditioning_mode'] != 'images_and_motion_video':
        raise Conflict('Package dispatch settings differ from the validated shared shot')
    references = ReferenceRegistry(store).approved_references(package['reference_pins'])
    if [entry['artifact_id'] for entry in references] != package['reference_ids']:
        raise Conflict('Package appearance references no longer match its approved pins')
    if [pin.model_dump(mode='json') for pin in shot.reference_pins] != package['reference_pins']:
        raise Conflict('Package reference pins differ from its shared shot plan')
    prompt = store.artifact(package['prompt_ref'])
    prompt_bytes = store.artifact_path(prompt['id']).read_bytes()
    if hashlib.sha256(prompt_bytes).hexdigest() != prompt['sha256'] or prompt_bytes.decode() != package['prompt']:
        raise Conflict('Package prompt no longer matches its reviewed artifact')
    receipt_artifact = store.artifact(package['motion_receipt_id'])
    receipt_bytes = store.artifact_path(receipt_artifact['id']).read_bytes()
    if hashlib.sha256(receipt_bytes).hexdigest() != receipt_artifact['sha256']:
        raise Conflict('Motion receipt no longer matches its recorded hash')
    receipt = json.loads(receipt_bytes)
    expected_input = {'scene': scene.model_dump(mode='json'), 'shot': shot.model_dump(mode='json'), 'motion': shot.motion.model_dump(mode='json')}
    source_hash = hashlib.sha256(json.dumps(expected_input, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()).hexdigest()
    video = store.artifact(package['motion_video_id'])
    if receipt.get('source_sha256') != source_hash or receipt.get('video_sha256') != video['sha256'] or not receipt.get('evaluation', {}).get('passed'):
        raise Conflict('Motion receipt no longer verifies this exact plan and video')
    if receipt.get('shot_id') != shot.id or receipt.get('scene_revision_id') != scene.id or receipt.get('fps') != shot.fps or receipt.get('frame_count') != shot.duration * shot.fps or receipt.get('duration_seconds') != shot.duration:
        raise Conflict('Motion receipt timeline differs from the shared shot duration and frame rate')
    _, pricing = await compile_reference_video(store, package['prompt'], package['reference_ids'], [video['id']],
        duration=package['duration'], resolution=package['resolution'])
    if abs(pricing['input_video_seconds'] - shot.duration) > 1 / shot.fps:
        raise Conflict('Decoded motion clip duration differs from the shared shot')
    if store.redactor.value(pricing) != package['pricing_estimate']:
        raise Conflict('Media or pricing estimate changed; prepare and review a new submission')


async def handle_shot_command(service, command, scene):
    from ghost_director.domain.models import SceneRevision
    from ghost_director.motion import render_motion, validate_motion
    store = service.store
    op, payload = command.operation, command.payload
    if op == 'update_shot_plan':
        return revise_shot(store, scene, payload)
    if op == 'generate_package':
        request = GeneratePackageRequest.model_validate(payload)
        package = store.get_record('packages', request.package_id)
        await validate_package_dispatch(store, scene, package, request.approval)
        prior = [take for take in store.list_records('takes') if take['package_id'] == package['id']]
        if prior:
            raise Conflict('This package already has a take; create a new plan/package for another attempt')
        if service.generation_lock.locked():
            raise Conflict('A generation workflow is already running')
        async with service.generation_lock:
            # A single package may be dispatched once even after a lost response.
            if any(job.get('run_id') == package['run_id'] and job.get('category') == 'video' for job in store.snapshot()['jobs']):
                raise Conflict('Package was already dispatched; inspect its job before creating another')
            if store.scene()['id'] != package['scene_revision_id']:
                raise Conflict('Scene changed during submission checks; prepare from the current reviewed plan')
            if not service.settings.allow_live:
                raise ValueError('Live calls are disabled; the reviewed request is ready for a separately authorized generation')
            store.append_event('package.generation_authorized', {'package_id': package['id'],
                **request.approval.model_dump(mode='json'), 'estimated_usd': package['pricing_estimate']['estimated_usd'],
                'reserve_usd': package['pricing_estimate']['reserve_usd']}, run_id=package['run_id'])
            try:
                video, job = await service.runner.video(package['prompt'], package['reference_ids'], duration=package['duration'],
                    resolution=package['resolution'], run_id=package['run_id'], model_override=package['model'], video_reference_ids=[package['motion_video_id']])
                take = await service.finish_take(video, job, package, scene)
                store.finish_run(package['run_id'])
                return {'take_id': take['id'], 'run_id': package['run_id']}
            except BaseException:
                store.finish_run(package['run_id'], 'failed')
                raise
    shot = ShotPlan.model_validate(store.get_record('shots', payload['shot_id']))
    if shot.scene_revision_id != scene.id:
        raise Conflict('Shot plan is historical; save it against the current scene first')
    validate_motion(scene, shot)
    run = store.create_run(metadata={'name': 'Technical motion' if op == 'render_motion' else 'Submission preview', 'shot_id': shot.id, 'scene_revision_id': scene.id})
    try:
        if op == 'render_motion':
            async with service.render_lock:
                artifacts = await render_motion(store, service.settings, scene, shot, run['id'])
            preview = {'id': new_id('motion'), 'run_id': run['id'], 'shot_id': shot.id, 'scene_revision_id': scene.id,
                       'plan_sha256': plan_digest(shot), **{f'{name}_id': artifact['id'] for name, artifact in artifacts.items()}, 'created_at': now_iso()}
            store.upsert_record('motion_previews', preview, event_type='motion.ready', run_id=run['id'])
            result = preview
        else:
            preview = next((item for item in reversed(store.list_records('motion_previews')) if item['shot_id'] == shot.id and item['plan_sha256'] == plan_digest(shot)), None)
            if preview is None:
                raise ValueError('Render and inspect this plan’s motion preview before compiling')
            result = await compile_package(store, scene, shot, preview, run_id=run['id'])
        store.finish_run(run['id'])
        return result
    except BaseException:
        store.finish_run(run['id'], 'failed')
        raise
