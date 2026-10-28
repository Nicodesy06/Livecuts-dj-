"""Vision-language model providers for the slow eye.

* ``OllamaProvider``  local and offline: talks to an Ollama server on this PC
  (``http://127.0.0.1:11434``) with the standard library only. Also lists,
  downloads (with progress) and removes models.
* ``ClaudeProvider``  online (opt-in): Claude API through the official
  ``anthropic`` SDK, imported lazily (optional dependency). Frames leave the PC.

Both return the parsed JSON object described by ``JUDGE_SCHEMA``.
"""

from __future__ import annotations

import base64
import json
import logging
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Callable, Optional

log = logging.getLogger(__name__)


class ProviderError(Exception):
    pass


JUDGE_SCHEMA = {
    "type": "object",
    "properties": {
        "cameras": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "n": {"type": "integer"},
                    "usable": {"type": "boolean"},
                    "interest": {"type": "number"},
                    "composition": {"type": "number"},
                    "moment": {"type": "string", "enum": ["none", "hands_on_mixer", "dj_gesture", "crowd_peak",
                                                          "lights", "dj_with_crowd"]},
                    "issue": {"type": "string"},
                    "what": {"type": "string"},
                },
                "required": ["n", "usable", "interest", "composition", "moment", "issue", "what"],
                "additionalProperties": False,
            },
        },
        "hold": {"type": "boolean"},
        "next_pick": {"type": "integer"},
        "drop_pick": {"type": "integer"},
        "reason": {"type": "string"},
        "note": {"type": "string"},
    },
    "required": ["cameras", "hold", "next_pick", "drop_pick", "reason", "note"],
    "additionalProperties": False,
}


@dataclass(frozen=True)
class ModelInfo:
    """A local model the user can choose, with honest pros and cons (shown in the UI)."""
    tag: str
    name: str
    size_gb: float
    pros: str
    cons: str
    recommended: bool = False


LOCAL_MODELS: tuple[ModelInfo, ...] = (
    ModelInfo("qwen3-vl:4b", "Qwen3-VL 4B", 3.3,
              "Best understanding of scenes among the small models; about 1-3 s per look on an RTX 4060.",
              "Uses about 4-5 GB of video memory while OBS is encoding; slow without a GPU.", True),
    ModelInfo("qwen3-vl:2b", "Qwen3-VL 2B", 1.9,
              "Lighter and faster; leaves more video memory to OBS.",
              "Notices fewer details (e.g. small hand gestures, distant crowd)."),
    ModelInfo("gemma3:4b", "Gemma 3 4B", 3.3,
              "Widely used and stable; good general descriptions.",
              "Somewhat less precise than Qwen3-VL on images."),
    ModelInfo("qwen3-vl:8b", "Qwen3-VL 8B", 6.2,
              "Most precise of the list.",
              "With OBS on an 8 GB card it can slow down encoding; only for GPUs with 12 GB or more."),
    ModelInfo("moondream", "Moondream 2", 1.7,
              "Smallest; runs even on modest PCs.",
              "Simple descriptions; often unreliable as a judge of shots."),
)

CLOUD_MODELS: tuple[ModelInfo, ...] = (
    ModelInfo("claude-opus-5-5", "Claude Opus 5.5", 0.0,
              "Best judgement of the scene and of the moment; 2-4 s per look.",
              "Highest cost per look; needs internet and an API key; frames leave the PC.", True),
    ModelInfo("claude-sonnet-5-5", "Claude Sonnet 5.5", 0.0,
              "Very good judgement at about half the cost.",
              "Needs internet and an API key; frames leave the PC."),
    ModelInfo("claude-haiku-5-5", "Claude Haiku 5.5", 0.0,
              "Cheapest and fastest.",
              "Less refined judgement; needs internet and an API key; frames leave the PC."),
)


# --------------------------------------------------------------------------- Ollama (local)
class OllamaProvider:
    kind = "local"

    def __init__(self, base_url: str = "http://127.0.0.1:11434", model: str = "qwen3-vl:4b") -> None:
        self.base_url = base_url.rstrip("/")
        self.model = model
        self._no_think = True          # ask thinking models not to think (lower latency)

    def _req(self, path: str, body: Optional[dict] = None, timeout: float = 5.0, method: str = ""):
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(self.base_url + path, data=data,
                                     method=method or ("POST" if data is not None else "GET"),
                                     headers={"Content-Type": "application/json"})
        return urllib.request.urlopen(req, timeout=timeout)

    def version(self, timeout: float = 2.0) -> Optional[str]:
        """Ollama version, or None when Ollama is not installed / not running."""
        try:
            with self._req("/api/version", timeout=timeout) as r:
                return json.loads(r.read()).get("version", "?")
        except (OSError, ValueError, urllib.error.URLError):
            return None

    def installed(self, timeout: float = 3.0) -> list[str]:
        try:
            with self._req("/api/tags", timeout=timeout) as r:
                return [m.get("name", "") for m in json.loads(r.read()).get("models", [])]
        except (OSError, ValueError, urllib.error.URLError):
            return []

    def has_model(self, tag: str, names: Optional[list[str]] = None) -> bool:
        names = self.installed() if names is None else names
        want = tag if ":" in tag else tag + ":latest"
        return any(n == tag or n == want for n in names)

    def pull(self, tag: str, progress: Callable[[str, float], None],
             cancelled: Callable[[], bool] = lambda: False) -> None:
        """Download a model; ``progress(status, fraction 0..1 or -1)``. Raises ProviderError."""
        try:
            with self._req("/api/pull", {"model": tag, "stream": True}, timeout=30.0) as r:
                for line in r:
                    if cancelled():
                        raise ProviderError("download cancelled")
                    if not line.strip():
                        continue
                    msg = json.loads(line)
                    if "error" in msg:
                        raise ProviderError(msg["error"])
                    total, done = msg.get("total") or 0, msg.get("completed") or 0
                    progress(msg.get("status", ""), done / total if total else -1.0)
        except (OSError, ValueError, urllib.error.URLError) as e:
            raise ProviderError(f"download failed: {e}") from None

    def delete(self, tag: str) -> None:
        try:
            self._req("/api/delete", {"model": tag}, timeout=10.0, method="DELETE").close()
        except (OSError, urllib.error.URLError) as e:
            raise ProviderError(f"cannot remove model: {e}") from None

    def judge(self, image_jpeg: bytes, system: str, prompt: str, timeout: float,
              schema: Optional[dict] = None) -> dict:
        body = {
            "model": self.model,
            "messages": [{"role": "system", "content": system},
                         {"role": "user", "content": prompt,
                          "images": [base64.b64encode(image_jpeg).decode("ascii")]}],
            "stream": False,
            "format": schema or JUDGE_SCHEMA,
            "options": {"temperature": 0},
            "keep_alive": "30m",
        }
        if self._no_think:
            body["think"] = False
        try:
            with self._req("/api/chat", body, timeout=timeout) as r:
                resp = json.loads(r.read())
        except urllib.error.HTTPError as e:
            detail = e.read().decode("utf-8", "replace")[:200]
            if self._no_think and "think" in detail.lower():
                self._no_think = False           # model without a thinking switch: retry plainly
                return self.judge(image_jpeg, system, prompt, timeout, schema)
            if e.code == 404:
                raise ProviderError(f"model {self.model} is not installed in Ollama") from None
            raise ProviderError(f"Ollama error {e.code}: {detail}") from None
        except (OSError, ValueError, urllib.error.URLError) as e:
            raise ProviderError(f"Ollama not reachable: {e}") from None
        content = (resp.get("message") or {}).get("content", "")
        try:
            return json.loads(content)
        except ValueError:
            raise ProviderError("model answer is not valid JSON") from None


# --------------------------------------------------------------------------- Claude API (cloud)
def cloud_available() -> bool:
    try:
        import anthropic  # noqa: F401
        return True
    except ImportError:
        return False


class ClaudeProvider:
    kind = "cloud"

    def __init__(self, api_key: str, model: str = "claude-opus-5-5") -> None:
        if not api_key:
            raise ProviderError("no Claude API key configured")
        try:
            import anthropic
        except ImportError:
            raise ProviderError("the 'anthropic' package is not installed") from None
        self._anthropic = anthropic
        self.client = anthropic.Anthropic(api_key=api_key, max_retries=1)
        self.model = model

    def judge(self, image_jpeg: bytes, system: str, prompt: str, timeout: float,
              schema: Optional[dict] = None) -> dict:
        anthropic = self._anthropic
        kwargs: dict = {}
        if self.model in ("claude-opus-5-5", "claude-sonnet-5-5"):
            # on a policy decline the API re-runs the request on a fallback model
            kwargs = {"betas": ["server-side-fallback-2026-07-01"], "fallbacks": "default"}
        try:
            response = self.client.with_options(timeout=timeout).beta.messages.create(
                model=self.model,
                max_tokens=4000,
                system=system,
                messages=[{"role": "user", "content": [
                    {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg",
                                                 "data": base64.standard_b64encode(image_jpeg).decode("ascii")}},
                    {"type": "text", "text": prompt},
                ]}],
                output_config={"effort": "low", "format": {"type": "json_schema", "schema": schema or JUDGE_SCHEMA}},
                **kwargs,
            )
        except anthropic.AuthenticationError:
            raise ProviderError("Claude API key rejected") from None
        except anthropic.PermissionDeniedError:
            raise ProviderError("Claude API key lacks permission for this model") from None
        except anthropic.NotFoundError:
            raise ProviderError(f"model {self.model} not available") from None
        except anthropic.RateLimitError:
            raise ProviderError("Claude API rate limit reached") from None
        except anthropic.APIStatusError as e:
            raise ProviderError(f"Claude API error {e.status_code}") from None
        except anthropic.APIConnectionError:
            raise ProviderError("no internet connection to the Claude API") from None
        if response.stop_reason == "refusal":
            raise ProviderError("the model declined to judge this frame")
        text = next((b.text for b in response.content if b.type == "text"), "")
        try:
            return json.loads(text)
        except ValueError:
            raise ProviderError("model answer is not valid JSON") from None
