import io
import os
import unittest
from unittest.mock import patch

import httpx
from fastapi.testclient import TestClient
from PIL import Image

from cardnews import api


class CardNewsRenderTest(unittest.TestCase):
    project = "https://test.supabase.co"
    job_id = "11111111-1111-4111-8111-111111111111"
    headers = {"x-cardnews-api-key": "test-key"}

    def setUp(self):
        env = patch.dict(os.environ, {
            "SUPABASE_URL": self.project, "CARDNEWS_INTERNAL_API_KEY": "test-key", "OPENAI_API_KEY": "",
        })
        env.start()
        self.addCleanup(env.stop)
        self.client = TestClient(api.app)

    def body(self, theme="minimal"):
        return {
            "job_id": self.job_id, "theme": theme, "store_name": "떡집 </script>", "menu_name": "쑥떡",
            "photos": [
                {"role": role, "download_url": f"{self.project}/storage/v1/object/sign/uploads/{role}.jpg?token=test"}
                for role in api.design.PHOTO_ROLES
            ],
            "uploads": [
                f"{self.project}/storage/v1/object/upload/sign/uploads/store/submission/generated/{self.job_id}-{i}.png?token=test"
                for i in range(1, 4)
            ],
        }

    def test_healthz_identifies_the_service_and_storage(self):
        response = self.client.get("/healthz")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {
            "status": "ok", "service": "postalk-cardnews", "mediaProvider": "supabase",
        })

    def test_rejects_missing_credentials_and_foreign_storage(self):
        body = self.body()
        self.assertEqual(self.client.post("/cardnews/renders", json=body).status_code, 401)
        body["photos"][0]["download_url"] = "https://other.example/photo"
        self.assertEqual(self.client.post("/cardnews/renders", headers=self.headers, json=body).status_code, 400)

    def test_both_themes_produce_three_real_pngs(self):
        for theme, template in [("minimal", "t1"), ("christmas", "x1")]:
            with self.subTest(theme=theme):
                uploads = {}

                def storage(request):
                    if request.method == "GET":
                        color = list(api.design.PHOTO_ROLES).index(request.url.path.split("/")[-1][:-4])
                        photo = io.BytesIO()
                        Image.new("RGB", (260, 320), (30 + color * 25, 60, 100)).save(photo, "JPEG")
                        return httpx.Response(200, content=photo.getvalue())
                    self.assertEqual(request.method, "PUT")
                    self.assertEqual(request.headers["content-type"], "image/png")
                    uploads[request.url.path.split("/uploads/")[-1]] = request.read()
                    return httpx.Response(200, json={})

                transfer = httpx.Client(transport=httpx.MockTransport(storage))
                with patch.object(api.httpx, "Client", return_value=transfer):
                    response = self.client.post("/cardnews/renders", headers=self.headers, json=self.body(theme))
                self.assertEqual(response.status_code, 200, response.text)
                self.assertEqual(response.json()["template"], template)
                self.assertEqual(len(uploads), 3)
                for card in response.json()["cards"]:
                    image = uploads[card["key"]]
                    self.assertEqual(len(image), card["bytes"])
                    with Image.open(io.BytesIO(image)) as rendered:
                        self.assertEqual(rendered.format, "PNG")
                        self.assertEqual(rendered.size, (1080, 1350))
                        self.assertGreater(len(rendered.getcolors(512) or [None] * 513), 5)
