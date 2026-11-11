#!/usr/bin/env python3
"""Export SpeechBrain's spoken language ID model to ONNX, once, off the device.

    python tools/export_langid.py <downloaded model dir> [out dir]

The model is ``speechbrain/lang-id-voxlingua107-ecapa`` (Apache 2.0), an ECAPA-TDNN trained
on VoxLingua107: 107 languages, including Hindi, English and 9 other Indian languages.
The ONNX file takes raw 16 kHz mono audio and returns a log-probability for every
language, with the filterbank features and normalisation built in. So the device needs
only onnxruntime, not PyTorch or SpeechBrain.

Run it where SpeechBrain and PyTorch are installed. Download the model first, pinned to the
revision this was checked on:

    huggingface-cli download speechbrain/lang-id-voxlingua107-ecapa \
        --revision 0253049ae131d6a4be1c4f0d8b0ff483a0f8c8e9 --local-dir <dir>

The export is checked against SpeechBrain's own output before it is written.
"""

from __future__ import annotations

import json
import os
import sys

import numpy as np
import torch

REVISION = "0253049ae131d6a4be1c4f0d8b0ff483a0f8c8e9"
ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
DEFAULT_OUT = os.path.join(ROOT, "local", "models", "lang-id-ecapa-onnx")


class ConvSTFT(torch.nn.Module):
    """SpeechBrain's STFT as a convolution with a fixed DFT basis.

    The ONNX exporter cannot export torch.stft as SpeechBrain calls it, but a convolution
    exports everywhere. Same output: [batch, frames, bins, (real, imag)].
    """

    def __init__(self, stft):
        super().__init__()
        assert stft.center and stft.pad_mode == "constant" and stft.onesided and not stft.normalized_stft
        assert stft.win_length == stft.n_fft
        n, self.hop, self.pad = stft.n_fft, stft.hop_length, stft.n_fft // 2
        bins = n // 2 + 1
        t = torch.arange(n, dtype=torch.float64)
        k = torch.arange(bins, dtype=torch.float64)[:, None]
        window = stft.window.to(torch.float64)
        real = window * torch.cos(2 * torch.pi * k * t / n)
        imag = -window * torch.sin(2 * torch.pi * k * t / n)
        self.register_buffer("basis", torch.cat([real, imag])[:, None, :].float())
        self.bins = bins

    def forward(self, wav):
        x = torch.nn.functional.pad(wav[:, None, :], (self.pad, self.pad))
        y = torch.nn.functional.conv1d(x, self.basis, stride=self.hop)       # [B, 2*bins, frames]
        y = y.reshape(y.shape[0], 2, self.bins, y.shape[-1])
        return y.permute(0, 3, 2, 1)                                          # [B, frames, bins, 2]


class LanguageId(torch.nn.Module):
    """Raw audio in, 107 log-probabilities out, the same steps as SpeechBrain's classify_batch."""

    def __init__(self, classifier):
        super().__init__()
        m = classifier.mods
        assert not m.compute_features.deltas and not m.compute_features.context
        self.stft = ConvSTFT(m.compute_features.compute_STFT)
        self.fbanks = m.compute_features.compute_fbanks
        self.embedding, self.classifier = m.embedding_model, m.classifier

    def forward(self, wav):  # wav: [1, samples], float32, 16 kHz
        from speechbrain.processing.features import spectral_magnitude

        feats = self.fbanks(spectral_magnitude(self.stft(wav)))
        # Sentence normalisation with one full-length clip: subtract the mean over time.
        feats = feats - feats.mean(dim=1, keepdim=True)
        emb = self.embedding(feats)
        return self.classifier(emb).squeeze(1)


def main() -> int:
    from speechbrain.inference.classifiers import EncoderClassifier

    model_dir = sys.argv[1]
    out = sys.argv[2] if len(sys.argv) > 2 else DEFAULT_OUT
    clf = EncoderClassifier.from_hparams(source=model_dir, savedir=model_dir, run_opts={"device": "cpu"})
    assert not clf.mods.mean_var_norm.std_norm, "the export assumes mean-only normalisation"
    net = LanguageId(clf).eval()

    rng = np.random.default_rng(0)
    probe = torch.from_numpy(rng.standard_normal(16000 * 3).astype(np.float32) * 0.05)[None]
    with torch.no_grad():
        reference = clf.classify_batch(probe)[0]
        mine = net(probe)
    gap = float((reference - mine).abs().max())
    assert gap < 1e-4, f"the wrapper does not match SpeechBrain ({gap})"

    os.makedirs(out, exist_ok=True)
    path = os.path.join(out, "model.onnx")
    torch.onnx.export(net, (probe,), path, input_names=["audio"], output_names=["log_probs"],
                      dynamic_axes={"audio": {1: "samples"}, "log_probs": {0: "batch"}},
                      opset_version=17, dynamo=False)

    import onnxruntime as ort

    session = ort.InferenceSession(path, providers=["CPUExecutionProvider"])
    for seconds in (1.0, 3.0, 8.0):
        clip = torch.from_numpy(rng.standard_normal(int(16000 * seconds)).astype(np.float32) * 0.05)[None]
        with torch.no_grad():
            expected = clf.classify_batch(clip)[0].numpy()
        got = session.run(None, {"audio": clip.numpy()})[0]
        gap = float(np.abs(expected - got).max())
        assert gap < 1e-3, f"ONNX differs from SpeechBrain on {seconds} s of audio ({gap})"
        print(f"ONNX matches SpeechBrain on {seconds:.0f} s of audio (largest gap {gap:.1e})")

    enc = clf.hparams.label_encoder
    labels = [enc.ind2lab[i].split(":")[0].strip() for i in range(len(enc.ind2lab))]
    with open(os.path.join(out, "labels.json"), "w", encoding="utf-8") as f:
        json.dump(labels, f)
    with open(os.path.join(out, "README.md"), "w", encoding="utf-8") as f:
        f.write(
            "# Spoken language ID\n\n"
            "ONNX export of [speechbrain/lang-id-voxlingua107-ecapa]"
            "(https://huggingface.co/speechbrain/lang-id-voxlingua107-ecapa), revision "
            f"`{REVISION}`, made with `tools/export_langid.py`.\n\n"
            "Licence: Apache 2.0. ECAPA-TDNN by SpeechBrain, trained on VoxLingua107 "
            "(Valk and Alumäe, 2021).\n\n"
            "Input `audio`: float32 [1, samples], 16 kHz mono. Output `log_probs`: [1, 107], "
            "in the order of `labels.json`.\n"
        )
    print(f"Wrote {out}: {os.path.getsize(path) / 1e6:.1f} MB, {len(labels)} languages")
    return 0


if __name__ == "__main__":
    sys.exit(main())
