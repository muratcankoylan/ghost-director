# Recorded Blender camera measurements

`blender-5.2.3-camera-proof.json` was captured on November 3, 2026 by `ghost_director/blender/build_set.py` using Blender 5.2.3 LTS `world_to_camera_view`. It contains three real camera transforms, five asymmetric world points per camera and measured pixel projections at 960×540.

The regression compares the independent Python projection implementation against these frozen measurements within 0.01 pixel. It does not run Blender during the test or certify a different Blender version. Rebuild the scene with the documented Blender workflow to collect new measurements; review coordinate changes before replacing this fixture.

Original measurement SHA-256: `8880cd402b6e3df4235bd0d09480ac6eb289edbd2189bbaca7c655362e13d2e4`. No production media or credentials are included.
