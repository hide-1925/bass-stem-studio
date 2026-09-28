"""Demucs (Meta, MIT) separator. Default model ``htdemucs_6s``: drums/bass/other/vocals/guitar/piano.

Note: the Demucs authors state the piano stem of htdemucs_6s is of limited quality.
"""

from __future__ import annotations

import math
import platform

import numpy as np

from . import SeparationCancelled, Separator, fit_length


class DemucsSeparator(Separator):
    name = "demucs"

    def __init__(self, model: str = "htdemucs_6s", device: str = "auto", shifts: int = 1, overlap: float = 0.25,
                 segment: float | None = None, **_):
        super().__init__(model, device)
        self.shifts = int(shifts)
        self.overlap = float(overlap)
        self.segment = segment
        self._model = None

    # ------------------------------------------------------------------
    def _device(self) -> str:
        import torch

        if self.device_pref == "cuda" or (self.device_pref == "auto" and torch.cuda.is_available()):
            if torch.cuda.is_available():
                return "cuda"
        return "cpu"

    def load(self):
        if self._model is None:
            from demucs.pretrained import get_model

            self._model = get_model(self.model_name)
            self._model.eval()
        return self._model

    @property
    def stems(self) -> list[str]:
        return list(self.load().sources)

    def env_info(self) -> dict:
        import torch

        from importlib.metadata import version

        dev = self._device()
        info = {
            "separator": self.name,
            "model": self.model_name,
            "demucs": version("demucs"),
            "torch": torch.__version__,
            "python": platform.python_version(),
            "device": dev,
            "cpu": cpu_name(),
            "threads": torch.get_num_threads(),
            "gpu": torch.cuda.get_device_name(0) if dev == "cuda" else None,
            "shifts": self.shifts,
            "overlap": self.overlap,
        }
        return info

    def separate(self, mix, sr, progress, cancelled):
        import torch
        from demucs.apply import apply_model

        model = self.load()
        if sr != model.samplerate:
            raise ValueError(f"expected {model.samplerate} Hz input, got {sr}")
        n = mix.shape[-1]
        wav = torch.from_numpy(np.ascontiguousarray(mix, dtype=np.float32))
        # Same normalization as demucs.separate / demucs.api
        ref = wav.mean(0)
        mean, std = ref.mean(), ref.std() + 1e-8
        wav = (wav - mean) / std

        segment = self.segment or float(getattr(model, "segment", 7.8))
        if hasattr(model, "models"):
            segment = min(float(getattr(m, "segment", segment)) for m in model.models)
            n_models = len(model.models)
        else:
            n_models = 1
        stride = int((1 - self.overlap) * segment * sr)
        padded = n + (int(0.5 * sr) if self.shifts else 0)
        expected = max(1, math.ceil(padded / stride)) * max(1, self.shifts) * n_models
        done = {"n": 0}

        def cb(d: dict):
            if cancelled():
                raise SeparationCancelled()
            if d.get("state") == "end":
                done["n"] += 1
                progress(min(0.99, done["n"] / expected), f"分離中 {done['n']}/{expected}")

        with torch.no_grad():
            out = apply_model(model, wav[None], shifts=self.shifts, split=True, overlap=self.overlap,
                              progress=False, device=self._device(), segment=self.segment, callback=cb)[0]
        out = out * std + mean
        stems = {}
        for i, name in enumerate(model.sources):
            stems[name] = fit_length(out[i].cpu().numpy().astype(np.float32), n)
        return stems


def cpu_name() -> str:
    try:
        import winreg

        key = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"HARDWARE\DESCRIPTION\System\CentralProcessor\0")
        return str(winreg.QueryValueEx(key, "ProcessorNameString")[0]).strip()
    except Exception:
        return platform.processor() or "unknown"
