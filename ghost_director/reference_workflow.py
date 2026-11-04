"""Director-controlled reference authoring using the existing budgeted Meta path."""
from io import BytesIO
from PIL import Image
from pydantic import Field, FiniteFloat, model_validator
from ghost_director.domain.models import Contract
from ghost_director.references import ReferenceRegistry, REFERENCE_TEMPLATES
from ghost_director.jobs.store import Conflict


class CropReference(Contract):
    asset_id: str
    expected_revision_id: str
    slot: str
    state: str = 'default'
    box: tuple[FiniteFloat, FiniteFloat, FiniteFloat, FiniteFloat]

    @model_validator(mode='after')
    def bounded_box(self):
        left, top, right, bottom = self.box
        if not 0 <= left < right <= 1 or not 0 <= top < bottom <= 1:
            raise ValueError('Crop coordinates must be ordered fractions between zero and one')
        return self


async def handle_reference_command(service, command):
    store, payload = service.store, command.payload
    registry = ReferenceRegistry(store)
    if command.operation == 'create_reference_asset':
        return registry.create_asset(payload)
    if command.operation == 'approve_reference':
        return registry.decide(payload['asset_id'], payload['revision_id'], payload['decision'], actor='local-operator', reason=payload.get('reason', ''))
    if command.operation == 'import_reference':
        return registry.attach_artifact(payload['asset_id'], payload['expected_revision_id'], payload['artifact_id'], slot=payload['slot'], state=payload.get('state', 'default'), provenance={'source': 'imported', 'notes': 'Existing production image imported as a draft; no approval inferred.'})
    if command.operation == 'crop_reference':
        request = CropReference.model_validate(payload)
        asset = registry.asset(request.asset_id)
        if asset['revision_id'] != request.expected_revision_id:
            raise Conflict('Reference changed before crop; inspect its current revision')
        version = registry.version(request.expected_revision_id)
        reference = next((r for r in version['references'] if r['slot'] == request.slot and r['state'] == request.state), None)
        if reference is None:
            raise ValueError('The selected reference slot is empty')
        source_id = reference['original_artifact_id']
        from ghost_director.references import inspect_image
        source = store.artifact(source_id)
        image, _ = inspect_image(store.artifact_path(source_id).read_bytes(), source['mime_type'])
        box = [round(value * (image.width if i % 2 == 0 else image.height)) for i, value in enumerate(request.box)]
        if box[2] - box[0] < 32 or box[3] - box[1] < 32:
            raise ValueError('The crop must retain at least 32 pixels in each dimension')
        output = BytesIO(); image.crop(box).save(output, format='PNG')
        artifact = store.publish_artifact(output.getvalue(), kind='image', mime_type='image/png', metadata={'name': asset['name'] + ' · adjusted ' + request.slot, 'source_artifact_id': source_id, 'crop_box': list(request.box)})
        return registry.attach_artifact(asset['id'], asset['revision_id'], artifact['id'], slot=request.slot, state=request.state, original_artifact_id=source_id, provenance={**reference['provenance'], 'parent_reference_ids': [reference['artifact_id']], 'notes': 'Operator-adjusted crop from original image; approval pending.', 'crop_box': list(request.box)})
    asset = registry.asset(payload['asset_id'])
    if asset['revision_id'] != payload['expected_revision_id']:
        raise Conflict('Reference changed before generation; inspect its current revision')
    if registry.version(asset['revision_id'])['references']:
        raise Conflict('This identity already has visual references. The configured image adapter does not yet support conditioning on those images; upload views derived from the established identity instead of regenerating it from text.')
    instruction = str(payload.get('prompt', '')).strip()
    if not 1 <= len(instruction) <= 12000:
        raise ValueError('Reference direction must contain 1–12000 characters')
    slots = REFERENCE_TEMPLATES[asset['category']]['required_slots']
    panels = len(slots)
    prompt = f"Original photographic {asset['category']} identity reference for {asset['name']}. {asset['description']}. {instruction}\n"
    prompt += (f'Exactly {panels} equal-width vertical panels, left to right: ' + ', '.join(slots) + '. No borders, text, captions or labels. '
               'Every panel depicts the SAME identity/design, materials and clothing, with no redesign between views. '
               'Photographic detail, neutral readable light; fit the specified view entirely in each panel.\n')
    prompt += '\n'.join(f'{index+1}: {REFERENCE_TEMPLATES[asset["category"]]["slots"][slot]}' for index, slot in enumerate(slots))
    run = store.create_run(metadata={'name': f'References · {asset["name"]}', 'asset_id': asset['id']})
    try:
        prompt_art = store.publish_artifact(prompt, kind='prompt', mime_type='text/plain', metadata={'name': 'Identity sheet direction', 'asset_id': asset['id']}, run_id=run['id'])
        sheet, _ = await service.runner.image(prompt, run_id=run['id'], name=asset['name'] + ' · original reference sheet')
        with Image.open(store.artifact_path(sheet['id'])) as image:
            for index, slot in enumerate(slots):
                panel = image.crop((round(index * image.width / panels), 0, round((index + 1) * image.width / panels), image.height))
                buffer = BytesIO(); panel.save(buffer, format='PNG')
                artifact = store.publish_artifact(buffer.getvalue(), kind='image', mime_type='image/png', metadata={'name': asset['name'] + ' · ' + slot, 'sheet_id': sheet['id'], 'slot': slot}, run_id=run['id'])
                asset = registry.attach_artifact(asset['id'], asset['revision_id'], artifact['id'], slot=slot, original_artifact_id=sheet['id'], provenance={'source': 'generated', 'prompt_ref': prompt_art['id'], 'parent_reference_ids': [sheet['id']], 'notes': 'Crop from a single generated identity sheet. Inspect consistency and framing before approval.', 'crop_box': [index / panels, 0, (index + 1) / panels, 1]}, run_id=run['id'])
        store.finish_run(run['id'])
        return {**asset, 'run_id': run['id'], 'sheet_id': sheet['id']}
    except BaseException:
        store.finish_run(run['id'], 'failed')
        raise
