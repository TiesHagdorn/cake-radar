import base64
import io
import logging
from typing import List

import requests
from PIL import Image
from pillow_heif import register_heif_opener


_PILLOW_TO_OPENAI = {'JPEG': 'image/jpeg', 'PNG': 'image/png', 'GIF': 'image/gif', 'WEBP': 'image/webp'}
_heif_registered = False


def _ensure_heif_registered():
    global _heif_registered
    if not _heif_registered:
        register_heif_opener()
        _heif_registered = True


def download_slack_images(files: list, slack_bot_token: str, max_images: int = 1) -> List[str]:
    """Download image attachments from a Slack message and return as base64 data URIs."""
    data_uris = []
    for f in files:
        if len(data_uris) >= max_images:
            break
        mimetype = f.get('mimetype', '')
        url = f.get('url_private_download') or f.get('url_private')
        if not mimetype.startswith('image/') or not url:
            continue
        try:
            headers = {'Authorization': f'Bearer {slack_bot_token}'}
            response = requests.get(url, headers=headers, timeout=10, allow_redirects=False)
            logging.debug(f"Image fetch status={response.status_code} url={url[:80]}")
            if response.is_redirect or response.is_permanent_redirect:
                redirect_url = response.headers.get('Location')
                if redirect_url:
                    logging.debug(f"Image redirect -> {redirect_url[:80]}")
                    response = requests.get(redirect_url, timeout=10)
            response.raise_for_status()
            content_type = response.headers.get('Content-Type', '')
            if not content_type.startswith('image/'):
                logging.debug(f"Auth'd request returned HTML, retrying without auth: {response.content[:200]!r}")
                response = requests.get(url, timeout=10, allow_redirects=True)
                response.raise_for_status()
                content_type = response.headers.get('Content-Type', '')
            if not content_type.startswith('image/'):
                logging.warning(f"Slack returned {content_type!r} (len={len(response.content)}) instead of image, skipping")
                continue
            try:
                _ensure_heif_registered()
                img = Image.open(io.BytesIO(response.content))
                out_mimetype = _PILLOW_TO_OPENAI.get(img.format, 'image/jpeg')
                out_format = img.format if img.format in _PILLOW_TO_OPENAI else 'JPEG'
                if out_format == 'JPEG':
                    img = img.convert('RGB')
                buf = io.BytesIO()
                img.save(buf, out_format)
                encoded = base64.b64encode(buf.getvalue()).decode('utf-8')
                data_uris.append(f"data:{out_mimetype};base64,{encoded}")
            except Exception as conv_err:
                logging.warning(f"Could not process {mimetype} image (Content-Type={content_type!r}, len={len(response.content)}), skipping: {conv_err}")
        except Exception as e:
            logging.error(f"Failed to download Slack image: {e}")
    return data_uris

