"""Pluggable LLM adapter for answer synthesis (plan 45).

Ported from YAAMS. Backends take a plaintext prompt and return plaintext —
distinct from the JSON-protocol ``judge`` backend used by signal seeding, so
synthesis gets its own ``synth_*`` config keys.

- DummyAdapter: deterministic, offline; used for tests and "no backend".
- SubprocessAdapter: pipes the prompt to any CLI's stdin, reads stdout.
- ClaudeCliAdapter / OllamaAdapter: convenience wrappers.

``adapter_from_ledger_config(cfg)`` reads ``synth_backend`` / ``synth_command``
/ ``synth_model``.
"""

from __future__ import annotations

import shlex
import subprocess
from dataclasses import dataclass
from typing import Protocol


@dataclass
class LLMResponse:
    text: str
    backend: str
    model: str | None = None


class LLMAdapter(Protocol):
    backend_name: str
    model_name: str | None

    def complete(
        self, prompt: str, *, max_tokens: int = 1024, temperature: float = 0.0
    ) -> LLMResponse: ...


class DummyAdapter:
    backend_name = "dummy"

    def __init__(self, model_name: str | None = None):
        self.model_name = model_name

    def complete(
        self, prompt: str, *, max_tokens: int = 1024, temperature: float = 0.0
    ) -> LLMResponse:
        head = prompt.splitlines()[0][:80] if prompt else ""
        return LLMResponse(
            text=(
                "ANSWER:\n[dummy adapter] no synthesis backend configured; "
                f"echoing prompt head: {head}\n\n"
                "CONFIDENCE: low\nno LLM backend configured\n\nGAPS:\n- none"
            ),
            backend=self.backend_name,
            model=self.model_name,
        )


class SubprocessAdapter:
    backend_name = "subprocess"

    def __init__(
        self,
        command: list[str],
        model_name: str | None = None,
        timeout: float = 120.0,
        encoding: str = "utf-8",
    ):
        if not command:
            raise ValueError("SubprocessAdapter requires a non-empty command")
        self.command = list(command)
        self.model_name = model_name
        self.timeout = timeout
        self.encoding = encoding

    def complete(
        self, prompt: str, *, max_tokens: int = 1024, temperature: float = 0.0
    ) -> LLMResponse:
        result = subprocess.run(
            self.command,
            input=prompt,
            capture_output=True,
            text=True,
            timeout=self.timeout,
            encoding=self.encoding,
        )
        if result.returncode != 0:
            raise RuntimeError(
                f"synth subprocess {self.command[0]!r} exited "
                f"{result.returncode}: {result.stderr.strip()}"
            )
        return LLMResponse(
            text=result.stdout.strip(),
            backend=self.backend_name,
            model=self.model_name,
        )


class ClaudeCliAdapter:
    """Drives ``claude -p --input-format text`` via stdin."""

    backend_name = "claude"

    def __init__(self, model_name: str | None = None, timeout: float = 120.0):
        self.model_name = model_name
        self.timeout = timeout

    def complete(
        self, prompt: str, *, max_tokens: int = 1024, temperature: float = 0.0
    ) -> LLMResponse:
        cmd = ["claude", "-p", "--input-format", "text"]
        if self.model_name:
            cmd += ["--model", self.model_name]
        result = subprocess.run(
            cmd, input=prompt, capture_output=True, text=True, timeout=self.timeout
        )
        if result.returncode != 0:
            raise RuntimeError(f"claude CLI exited {result.returncode}: {result.stderr.strip()}")
        return LLMResponse(text=result.stdout.strip(), backend=self.backend_name, model=self.model_name)


class OllamaAdapter:
    backend_name = "ollama"

    def __init__(
        self,
        model_name: str | None = "llama3.1",
        host: str = "http://localhost:11434",
        timeout: float = 60.0,
    ):
        self.model_name = model_name
        self.host = host.rstrip("/")
        self.timeout = timeout

    def complete(
        self, prompt: str, *, max_tokens: int = 1024, temperature: float = 0.0
    ) -> LLMResponse:
        import httpx

        body = {
            "model": self.model_name,
            "prompt": prompt,
            "stream": False,
            "options": {"temperature": temperature, "num_predict": max_tokens},
        }
        response = httpx.post(f"{self.host}/api/generate", json=body, timeout=self.timeout)
        response.raise_for_status()
        data = response.json()
        return LLMResponse(
            text=str(data.get("response", "")).strip(),
            backend=self.backend_name,
            model=self.model_name,
        )


def adapter_from_ledger_config(cfg, *, backend: str | None = None) -> LLMAdapter:
    """Build a synthesis adapter from ledger config (plan 45).

    ``backend`` overrides ``cfg.synth_backend`` when provided. Subprocess
    commands come from ``cfg.synth_command`` (shlex-split). ``dummy`` is the
    offline default.
    """
    resolved = (backend or getattr(cfg, "synth_backend", "dummy") or "dummy").strip().lower()
    model = getattr(cfg, "synth_model", None) or None
    timeout = float(getattr(cfg, "synth_timeout", 120.0) or 120.0)

    if resolved == "claude":
        return ClaudeCliAdapter(model_name=str(model) if model else None, timeout=timeout)
    if resolved == "ollama":
        return OllamaAdapter(
            model_name=str(model or "llama3.1"),
            host=str(getattr(cfg, "synth_host", "") or "http://localhost:11434"),
            timeout=timeout,
        )
    if resolved == "subprocess":
        command = str(getattr(cfg, "synth_command", "") or "").strip()
        if not command:
            raise ValueError("synth_backend=subprocess requires synth_command")
        return SubprocessAdapter(
            command=shlex.split(command),
            model_name=str(model) if model else None,
            timeout=timeout,
        )
    if resolved == "dummy":
        return DummyAdapter(model_name=str(model) if model else None)
    raise ValueError(f"Unknown synth backend: {resolved}")
