import logging
import os
from hmac import compare_digest
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Literal
from urllib.parse import parse_qs, unquote, urlsplit
from uuid import UUID

import httpx
from fastapi import FastAPI, Header, HTTPException
from openai import OpenAI
from pydantic import BaseModel, Field

from . import design
from .render import render_cards

app = FastAPI(title="POSTALK Card News")
logger = logging.getLogger(__name__)
MAX_IMAGE_BYTES = 10 * 1024 * 1024
PhotoRole = Literal["bowl", "zoom", "food1", "food2", "store1", "store2"]


class Photo(BaseModel):
    role: PhotoRole
    download_url: str = Field(max_length=4096)


class RenderRequest(BaseModel):
    job_id: UUID
    theme: Literal["minimal", "christmas"] = "minimal"
    store_name: str = Field(min_length=1, max_length=200)
    area: str = Field(default="", max_length=200)
    menu_name: str = Field(default="", max_length=200)
    direction: str = Field(default="", max_length=500)
    photos: list[Photo] = Field(min_length=6, max_length=6)
    uploads: list[str] = Field(min_length=3, max_length=3)


def object_key(url: str, *, upload: bool = False) -> str:
    base = urlsplit(os.environ.get("SUPABASE_URL", ""))
    target = urlsplit(url)
    endpoint = "object/upload/sign" if upload else "object/sign"
    prefix = f"/storage/v1/{endpoint}/uploads/"
    if (base.scheme != "https" or not base.netloc
            or (target.scheme, target.netloc) != (base.scheme, base.netloc)
            or not target.path.startswith(prefix) or not parse_qs(target.query).get("token") or target.fragment):
        raise ValueError("Invalid signed storage URL")
    key = unquote(target.path[len(prefix):])
    if "\\" in key or any(part in ("", ".", "..") for part in key.split("/")):
        raise ValueError("Invalid storage key")
    return key


def download_image(client: httpx.Client, url: str, path: Path) -> None:
    with client.stream("GET", url) as response, path.open("wb") as output:
        response.raise_for_status()
        size = 0
        for chunk in response.iter_bytes():
            size += len(chunk)
            if size > MAX_IMAGE_BYTES:
                raise ValueError("Photo exceeds the upload limit")
            output.write(chunk)
        if not size:
            raise ValueError("Photo is empty")


def build_style(body: RenderRequest, photos: dict, shop: dict) -> tuple[dict, dict]:
    profile = {"main_food": body.menu_name}
    default = {"template": design.THEMES[body.theme]["default"], "fallback": True}
    if not os.environ.get("OPENAI_API_KEY"):
        return profile, default
    with OpenAI(timeout=15.0, max_retries=0) as client:
        detected = design.analyze_photos(client, photos, shop)
        if not detected.get("error"):
            profile = detected
        try:
            return profile, design.make_style(client, body.direction, shop, profile=profile, theme=body.theme)
        except (TypeError, AttributeError, ValueError):
            return profile, default


@app.get("/healthz")
def health():
    return {"status": "ok", "service": "postalk-cardnews", "mediaProvider": "supabase"}


@app.post("/cardnews/renders")
def render(body: RenderRequest, x_cardnews_api_key: str = Header(default="")):
    secret = os.environ.get("CARDNEWS_INTERNAL_API_KEY", "").strip()
    if not secret:
        raise HTTPException(503, "Card news service is not configured")
    if not compare_digest(secret.encode(), x_cardnews_api_key.encode()):
        raise HTTPException(401, "Invalid credentials")
    try:
        if {photo.role for photo in body.photos} != set(design.PHOTO_ROLES):
            raise ValueError("Expected all six photo roles")
        keys = [object_key(photo.download_url) for photo in body.photos]
        if len(set(keys)) != 6:
            raise ValueError("Expected six distinct photos")
        destinations = [object_key(url, upload=True) for url in body.uploads]
        root = destinations[0].rsplit("/", 1)[0]
        if not root.endswith("/generated") or destinations != [
            f"{root}/{body.job_id}-{i}.png" for i in range(1, 4)
        ]:
            raise ValueError("Invalid output paths")
    except ValueError as error:
        raise HTTPException(400, str(error)) from error

    try:
        with TemporaryDirectory(prefix="postalk-cardnews-") as temp, httpx.Client(
            timeout=15.0, trust_env=False, follow_redirects=False
        ) as client:
            root = Path(temp)
            photos = {photo.role: root / f"{photo.role}.photo" for photo in body.photos}
            for photo in body.photos:
                download_image(client, photo.download_url, photos[photo.role])
            shop = {"name": body.store_name, "area": body.area}
            profile, style = build_style(body, photos, shop)
            paths = render_cards(photos, shop, profile, style, root)
            results = []
            for path, url, key in zip(paths, body.uploads, destinations, strict=True):
                size = path.stat().st_size
                if not 0 < size <= MAX_IMAGE_BYTES:
                    raise ValueError("Card exceeds the storage limit")
                with path.open("rb") as content:
                    client.put(url, content=iter(lambda: content.read(1024 * 1024), b""), headers={
                        "Content-Type": "image/png", "Content-Length": str(size), "x-upsert": "true",
                    }).raise_for_status()
                results.append({"key": key, "bytes": size})
            return {"cards": results, "template": style["template"], "fallback": bool(style.get("fallback"))}
    except Exception as error:
        logger.warning("Card news job %s failed (%s)", body.job_id, type(error).__name__)
        raise HTTPException(502, "Unable to create card news") from error
