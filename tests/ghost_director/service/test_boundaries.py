"""No-provider tests of concurrent editing and creative acceptance boundaries."""

import asyncio
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from ghost_director.config import Settings
from ghost_director.domain.models import Camera, Entity, SceneRevision, Take, ShotPlan
from ghost_director.jobs.store import Conflict, ProductionStore
from ghost_director.service import ProductionService


class ServiceBoundaryTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = TemporaryDirectory()
        self.store = ProductionStore(Path(self.tmp.name), opening_entries=())
        self.service = ProductionService(self.store, Settings("", "", "", False, Path(self.tmp.name)))
        self.scene = SceneRevision(id="scene-original", entities=[Entity(id="cup", name="Cup", kind="prop")], cameras=[Camera(id="camera", name="Camera", position=(0, -4, 2), target=(0, 0, 1))])
        self.store.publish_scene(self.scene.model_dump(mode="json"), expected_revision=None, actor="test")

    async def asyncTearDown(self):
        await self.service.runner.http.close()
        self.tmp.cleanup()

    def take(self, *, package_scene="scene-original"):
        self.store.upsert_record('shots',ShotPlan(id='shot',name='Shot',scene_revision_id='scene-original',camera_id='camera').model_dump(mode='json'))
        package = {"id": "package", "scene_revision_id": package_scene, "proposed_patch": [{"entity_id": "cup", "position": [2, 0, 0]}]}
        self.store.upsert_record("packages", package)
        take = Take(id="take", shot_id="shot", scene_revision_id="scene-original", package_id="package", job_id="job", artifact_id="video")
        self.store.upsert_record("takes", take.model_dump(mode="json"))

    def command(self, operation, payload, *, expected="scene-original", command_id="command"):
        return {"id": command_id, "operation": operation, "expected_revision": expected, "payload": payload}

    async def test_two_commands_using_same_scene_revision_have_one_winner(self):
        before = self.store.snapshot()["last_seq"]
        commands = [self.command("apply_scene", {"patches": [{"entity_id": "cup", "position": [i, 0, 0]}]}, command_id=f"change-{i}") for i in (1, 2)]
        results = await asyncio.gather(*(self.service.handle(command) for command in commands), return_exceptions=True)
        self.assertEqual(sum(isinstance(result, dict) for result in results), 1)
        self.assertEqual(sum(isinstance(result, Conflict) for result in results), 1)
        self.assertEqual(sum(event["type"] == "revision.accepted" for event in self.store.events(before)), 1)
        self.assertEqual(self.store.scene()["parent_id"], "scene-original")

    async def test_editing_remains_live_during_generation_but_input_is_frozen(self):
        started,release=asyncio.Event(),asyncio.Event()
        captured=[]
        async def generation(shot_id,scene,**options):
            captured.append(scene);started.set();await release.wait()
            return {'source_scene':scene.id}
        self.service.generate=generation
        pending=asyncio.create_task(self.service.handle(self.command('generate',{'shot_id':'shot'})))
        try:
            await started.wait()
            with self.assertRaisesRegex(Conflict,'already running'):
                await self.service.handle(self.command('generate',{'shot_id':'shot'},command_id='second'))
            await asyncio.wait_for(self.service.handle(self.command('apply_scene',{'patches':[{'entity_id':'cup','position':[2,0,0]}]})),timeout=1)
            self.assertEqual(captured[0].entities[0].position,(0,0,0))
            self.assertEqual(self.store.scene()['entities'][0]['position'],[2,0,0])
        finally:
            release.set();await pending

    async def test_stale_command_creates_no_side_effects(self):
        before = self.store.snapshot()
        with self.assertRaises(Conflict):
            await self.service.handle(self.command("apply_scene", {"patches": [{"entity_id": "cup", "position": [9, 0, 0]}]}, expected="stale"))
        self.assertEqual(self.store.snapshot(), before)

    async def test_invalid_saved_shot_cannot_publish_its_camera_first(self):
        before = self.store.snapshot()
        camera = {"position": [1, -4, 2], "target": [0, 0, 1], "focal_length_mm": 50, "sensor_width_mm": 36, "aspect_ratio": 16 / 9}
        with self.assertRaises(ValueError):
            await self.service.handle(self.command("save_shot", {"camera": camera, "name": "Invalid duration", "duration": 31}))
        self.assertEqual(self.store.snapshot(), before)

    async def test_acceptance_requires_exact_submitted_package_id(self):
        self.take()
        before = self.store.snapshot()
        with self.assertRaisesRegex(Conflict, "package"):
            await self.service.handle(self.command("accept_take", {"take_id": "take", "package_id": "different-package"}))
        self.assertEqual(self.store.snapshot(), before)

    async def test_acceptance_applies_immutable_package_instead_of_client_patch(self):
        self.take()
        package = self.store.get_record("packages", "package")
        result = await self.service.handle(self.command("accept_take", {"take_id": "take", "package_id": "package", "proposed_patch": [{"entity_id": "cup", "position": [99, 0, 0]}]}))
        self.assertEqual(self.store.scene()["entities"][0]["position"], [2.0, 0.0, 0.0])
        self.assertEqual(self.store.get_record("packages", "package"), package)
        self.assertEqual(self.store.get_record("takes", "take")["status"], "accepted")
        self.assertEqual(result["take_id"], "take")
        with self.assertRaises(Conflict):
            await self.service.handle(self.command("accept_take", {"take_id": "take"}, expected=result["revision_id"]))

    async def test_package_from_other_scene_cannot_be_accepted_on_current_head(self):
        self.take(package_scene="other-scene")
        before = self.store.snapshot()
        with self.assertRaisesRegex(Conflict, "scene revision"):
            await self.service.handle(self.command("accept_take", {"take_id": "take", "package_id": "package"}))
        self.assertEqual(self.store.snapshot(), before)

    async def test_rejection_keeps_scene_and_requires_reason(self):
        self.take()
        with self.assertRaises(ValueError):
            await self.service.handle(self.command("reject_take", {"take_id": "take", "reason": ""}))
        self.assertEqual(self.store.get_record("takes", "take")["status"], "unreviewed")
        await self.service.handle(self.command("reject_take", {"take_id": "take", "package_id": "package", "reason": "Cup identity changed"}))
        self.assertEqual(self.store.scene()["id"], "scene-original")
        self.assertEqual(self.store.get_record("takes", "take")["status"], "rejected")


if __name__ == "__main__":
    unittest.main()
