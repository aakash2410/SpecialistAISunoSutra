"""The offline answer loop, as a board-agnostic engine.

This is the reusable core shared by the on-device application and the laptop
demo. Input *capture* (ASR / OCR / buttons / camera) and output *playback*
(ALSA) are board-specific and live in the application; everything between —
embed -> retrieve -> ground -> LLM -> MT — lives here.

    request (text from ASR or OCR)
        -> Embed.embed_one(query)
        -> VectorIndex.search(top_k)
        -> build_grounding (refuse if not covered)
        -> LLM (grounded; may also refuse)
        -> MT into target language
    => AnswerResult (text + citations + timings [+ audio if a TTS client is given])

No step touches the network. The LLM, embedder, and index all sit on local
storage. Every non-refusal answer carries DIKSHA citations.
"""

from __future__ import annotations

import logging
import queue
import threading
import time
from dataclasses import dataclass, field
from typing import Callable, Iterator, List, Optional

from ..models.embed import DEFAULT_MODEL_NAME, Embed
from .grounding import (
    REFUSAL_TEXT,
    build_grounding,
    clean_answer,
    is_refusal_response,
    iter_sentences,
)
from .vector_index import Retrieved, VectorIndex

logger = logging.getLogger(__name__)

# A grounded LLM client: takes (system_prompt, user_prompt), returns the answer.
LLMClient = Callable[[str, str], str]
# A streaming grounded LLM client: takes (system_prompt, user_prompt), yields text chunks.
StreamLLMClient = Callable[[str, str], "Iterator[str]"]
# A translation client: takes (text, src_lang, tgt_lang), returns translated text.
MTClient = Callable[[str, str, str], str]
# A TTS client: takes (text, language), returns audio bytes (e.g. WAV).
TTSClient = Callable[[str, str], bytes]


@dataclass
class AnswerResult:
    refused: bool
    intent: str
    request: str
    target_language: str
    answer_en: str = ""
    answer_localized: str = ""
    citations: List[str] = field(default_factory=list)
    passages: List[Retrieved] = field(default_factory=list)
    audio: Optional[bytes] = None
    reason: str = ""
    timings: dict = field(default_factory=dict)

    @property
    def spoken_text(self) -> str:
        return self.answer_localized or self.answer_en


@dataclass
class SentenceResult:
    """One sentence of a streamed answer: text (English + localized) and its
    synthesized audio, ready to be played while the next sentence is produced."""
    index: int
    text_en: str
    text_localized: str
    audio: Optional[bytes] = None
    refused: bool = False
    citations: List[str] = field(default_factory=list)   # populated on the first item
    reason: str = ""

    @property
    def spoken_text(self) -> str:
        return self.text_localized or self.text_en


class SpecialistEngine:
    def __init__(
        self,
        index_dir: str,
        embedder: Optional[Embed] = None,
        llm: Optional[LLMClient] = None,
        mt: Optional[MTClient] = None,
        tts: Optional[TTSClient] = None,
        stream_llm: Optional[StreamLLMClient] = None,
        top_k: int = 3,
        min_score: Optional[float] = None,
    ):
        self.index = VectorIndex(index_dir)
        self.embedder = embedder or Embed(model_name=self.index.embedder_name or DEFAULT_MODEL_NAME)
        # The TF-IDF fallback carries fitted state baked next to the index.
        if self.embedder.backend == "tfidf" and Embed.has_state(index_dir):
            self.embedder.load_state(index_dir)
        self._check_embedder_matches_index()
        self.llm = llm or extractive_llm  # extractive fallback keeps it offline+runnable
        self.stream_llm = stream_llm      # optional; else stream_answer pseudo-streams
        self.mt = mt
        self.tts = tts
        self.top_k = top_k
        # Threshold precedence: explicit arg > value baked in the manifest > grounding default.
        if min_score is None:
            min_score = self.index.manifest.get("recommended_min_score")
        self.min_score = min_score

    def _check_embedder_matches_index(self):
        baked = self.index.embedder_name
        live = getattr(self.embedder, "model_name", None)
        if baked and live and baked != live:
            logger.warning(
                "Embedder mismatch: index built with '%s' but query uses '%s'. "
                "Retrieval quality will suffer; rebuild the index or match the model.",
                baked, live,
            )

    def prepare(self, request: str):
        """Embed + retrieve + ground WITHOUT calling the LLM.

        Returns the GroundingResult so a caller can stream the LLM itself (see
        the streaming voice loop). ``g.refused`` means the retrieval gate blocked
        it; otherwise use ``g.system_prompt`` / ``g.user_prompt``.
        """
        qvec = self.embedder.embed_one(request, kind="query")
        passages = self.index.search(qvec, top_k=self.top_k)
        kwargs = {} if self.min_score is None else {"min_score": self.min_score}
        return build_grounding(request, passages, **kwargs)

    def stream_answer(self, request: str, target_language: str = "en") -> Iterator[SentenceResult]:
        """Yield the answer one sentence at a time (embed -> retrieve -> ground ->
        stream LLM -> per-sentence MT + TTS).

        Each yielded SentenceResult carries its synthesized audio, so a caller can
        play sentence N while this generator produces N+1 (see ``speak_stream``).
        Uses the streaming LLM if one was provided; otherwise it pseudo-streams by
        sentence-splitting a single full answer. Refusals (retrieval gate or the
        LLM leading with the sentinel) yield a single refused SentenceResult.
        """
        def _localize(text: str) -> str:
            if self.mt and target_language and target_language.lower() != "en":
                try:
                    return self.mt(text, "EN", target_language)
                except Exception as e:  # noqa: BLE001
                    logger.warning("stream MT failed (%s)", e)
            return text

        def _audio(text: str) -> Optional[bytes]:
            if self.tts:
                try:
                    return self.tts(text, target_language)
                except Exception as e:  # noqa: BLE001
                    logger.warning("stream TTS failed (%s)", e)
            return None

        def _refusal(reason: str) -> SentenceResult:
            loc = _localize(REFUSAL_TEXT)
            return SentenceResult(0, REFUSAL_TEXT, loc, _audio(loc), refused=True, reason=reason)

        g = self.prepare(request)
        if g.refused:
            yield _refusal(g.reason)
            return

        if self.stream_llm is not None:
            chunks = self.stream_llm(g.system_prompt, g.user_prompt)
        else:
            chunks = iter([self.llm(g.system_prompt, g.user_prompt)])

        idx = 0
        for i, sentence in enumerate(iter_sentences(chunks)):
            if i == 0 and is_refusal_response(sentence):
                yield _refusal("LLM reported no answer in source")
                return
            text = clean_answer(sentence)
            if not text:
                continue
            loc = _localize(text)
            yield SentenceResult(
                index=idx, text_en=text, text_localized=loc, audio=_audio(loc),
                citations=g.citations if idx == 0 else [],
            )
            idx += 1

    def answer(self, request: str, target_language: str = "en") -> AnswerResult:
        t = {}
        t0 = time.time()

        qvec = self.embedder.embed_one(request, kind="query")
        t["embed"] = time.time() - t0

        ts = time.time()
        passages = self.index.search(qvec, top_k=self.top_k)
        t["retrieve"] = time.time() - ts

        kwargs = {} if self.min_score is None else {"min_score": self.min_score}
        g = build_grounding(request, passages, **kwargs)

        if g.refused:
            return self._finalize_refusal(request, target_language, g, passages, t)

        ts = time.time()
        llm_text = self.llm(g.system_prompt, g.user_prompt).strip()
        t["llm"] = time.time() - ts

        # Generation gate: the model says the sources don't cover it.
        if is_refusal_response(llm_text):
            snippet = (llm_text or "").strip()[:100] or "(empty response)"
            g.reason = f"LLM reported insufficient information (said: {snippet!r})."
            return self._finalize_refusal(request, target_language, g, passages, t)

        # Strip any leaked refusal note the model appended to a valid answer.
        llm_text = clean_answer(llm_text)
        answer_localized = llm_text
        if self.mt and target_language and target_language.lower() != "en":
            ts = time.time()
            try:
                answer_localized = self.mt(llm_text, "EN", target_language)
            except Exception as e:  # noqa: BLE001
                logger.warning("MT failed (%s); falling back to English answer", e)
            t["mt"] = time.time() - ts

        result = AnswerResult(
            refused=False,
            intent=g.intent,
            request=request,
            target_language=target_language,
            answer_en=llm_text,
            answer_localized=answer_localized,
            citations=g.citations,
            passages=g.passages,  # relevance-filtered set that was actually grounded on
            timings=t,
        )
        self._maybe_tts(result)
        t["total"] = time.time() - t0
        return result

    def _finalize_refusal(self, request, target_language, g, passages, t) -> AnswerResult:
        localized = REFUSAL_TEXT
        if self.mt and target_language and target_language.lower() != "en":
            try:
                localized = self.mt(REFUSAL_TEXT, "EN", target_language)
            except Exception:  # noqa: BLE001
                pass
        result = AnswerResult(
            refused=True,
            intent=g.intent,
            request=request,
            target_language=target_language,
            answer_en=REFUSAL_TEXT,
            answer_localized=localized,
            reason=g.reason,
            passages=passages,
            timings=t,
        )
        self._maybe_tts(result)
        t["total"] = sum(v for k, v in t.items() if k != "total")
        return result

    def _maybe_tts(self, result: AnswerResult):
        if self.tts:
            try:
                ts = time.time()
                result.audio = self.tts(result.spoken_text, result.target_language)
                result.timings["tts"] = time.time() - ts
            except Exception as e:  # noqa: BLE001
                logger.warning("TTS failed (%s)", e)


def extractive_llm(system_prompt: str, user_prompt: str) -> str:
    """Dependency-free 'grounded LLM' for the demo and offline fallback.

    It does not generate; it extracts. It returns the first source passage
    embedded in the system prompt, which is genuinely grounded (the passage is
    real DIKSHA text). This lets the full loop run without ollama while still
    honouring the only-from-source contract. On the device, pass a real grounded
    LLM client (see app/specialist_app.py) instead.
    """
    marker = "[1] ("
    idx = system_prompt.find(marker)
    if idx == -1:
        return "NO_ANSWER_IN_SOURCE"
    after = system_prompt[idx:]
    nl = after.find("\n")
    body = after[nl + 1 :] if nl != -1 else after
    end = body.find("\n\n[2] (")
    if end != -1:
        body = body[:end]
    return body.strip()


def speak_stream(sentences: Iterator[SentenceResult], play_fn: Callable[[bytes], None],
                 on_sentence: Optional[Callable[[SentenceResult], None]] = None):
    """Play a streamed answer with the audio overlapping generation.

    Consumes ``engine.stream_answer(...)``: a background thread plays each
    sentence's audio in order, while this loop keeps pulling the next sentence
    from the engine (which runs LLM + MT + TTS for it). ``play_fn(audio_bytes)``
    plays one clip (blocking) — board-specific, so the caller supplies it.
    ``on_sentence`` is called for each SentenceResult (e.g. to print it).

    Returns (results, first_audio_seconds) where first_audio_seconds is the delay
    from start to the first spoken audio — the responsiveness metric that matters.
    """
    q: "queue.Queue" = queue.Queue()
    _STOP = object()
    marks = {"t0": time.time(), "first": None}

    def _player():
        while True:
            item = q.get()
            if item is _STOP:
                break
            if marks["first"] is None:
                marks["first"] = time.time() - marks["t0"]
            try:
                play_fn(item)
            except Exception:  # noqa: BLE001
                logger.warning("playback failed", exc_info=True)

    t = threading.Thread(target=_player, daemon=True)
    t.start()
    results: List[SentenceResult] = []
    try:
        for sr in sentences:
            if on_sentence:
                on_sentence(sr)
            results.append(sr)
            if sr.audio:
                q.put(sr.audio)
    finally:
        q.put(_STOP)
        t.join()
    return results, marks["first"]
