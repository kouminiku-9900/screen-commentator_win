from __future__ import annotations

import base64
import io

from mss import mss
from PIL import Image

from .models import CapturedFrame


class ScreenCaptureService:
    def __init__(self, thumbnail_size: int, jpeg_quality: int) -> None:
        self.thumbnail_size = thumbnail_size
        self.jpeg_quality = jpeg_quality

    def grab_primary_display(self) -> CapturedFrame:
        with mss() as sct:
            monitor = sct.monitors[1]
            raw = sct.grab(monitor)

        image = Image.frombytes("RGB", raw.size, raw.rgb)
        buffer = io.BytesIO()
        image.save(buffer, format="JPEG", quality=self.jpeg_quality, optimize=True)
        thumbnail = image.resize(
            (self.thumbnail_size, self.thumbnail_size),
            Image.Resampling.BILINEAR,
        ).convert("RGB")
        return CapturedFrame(
            jpeg_base64=base64.b64encode(buffer.getvalue()).decode("ascii"),
            thumbnail_rgb=thumbnail.tobytes(),
            width=image.width,
            height=image.height,
        )
