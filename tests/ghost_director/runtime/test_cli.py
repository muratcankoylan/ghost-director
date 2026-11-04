"""Exercise the actual CLI assembly without binding a socket or using providers."""
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

from ghost_director.cli import main
from ghost_director.config import Settings


class CliOriginTests(unittest.TestCase):
    def test_configured_port_serves_browser_module_origin_without_opening_external_origins(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            assets = root / "web/dist/assets"
            assets.mkdir(parents=True)
            (assets.parent / "index.html").write_text('<script type="module" crossorigin src="/assets/app.js"></script>')
            (assets / "app.js").write_text('document.body.textContent = "fixture";')
            settings = Settings("", "", "", False, root / "production")
            launched = []

            def serve(app, **options):
                launched.append(options)
                with TestClient(app, base_url="http://127.0.0.1:8001") as client:
                    self.assertEqual(client.get("/").status_code, 200)
                    for origin in ("http://127.0.0.1:8001", "http://localhost:8001", "http://127.0.0.1:5173", "http://localhost:5173"):
                        response = client.get("/assets/app.js", headers={"Origin": origin})
                        self.assertEqual(response.status_code, 200)
                        self.assertEqual(response.headers["access-control-allow-origin"], origin)
                    for origin in ("https://attacker.test", "http://127.0.0.1.attacker.test:8001", "http://127.0.0.1:8002", "null"):
                        self.assertEqual(client.get("/assets/app.js", headers={"Origin": origin}).status_code, 403)
                    self.assertEqual(client.get("/health", headers={"Host": "attacker.test"}).status_code, 400)

            with patch("sys.argv", ["ghost-director", "serve", "--port", "8001"]), patch("ghost_director.cli.load_settings", return_value=settings), patch("ghost_director.config.REPO", root), patch("ghost_director.service.ProductionService.initialize"), patch("httpx.AsyncClient.request", side_effect=AssertionError("Provider access is forbidden")), patch("uvicorn.run", side_effect=serve):
                main()
            self.assertEqual(launched[0]["host"], "127.0.0.1")
            self.assertEqual(launched[0]["port"], 8001)


if __name__ == "__main__":
    unittest.main()
