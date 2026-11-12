"""The offline answer loop, as a board-agnostic engine.

This is the core behind the voice loop. Input *capture*
(ASR / OCR) and output *playback* live in those callers; everything between,
embed -> retrieve -> ground -> LLM -> MT, lives here.

    request (text from ASR or OCR)
        -> Embed.embed_one(query)
        -> refuse questions about live information ("the price of gold today")
        -> VectorIndex.search, keep the best top_k
        -> a topic word the index has never seen: ask again, offering a textbook word
           spelt like it ("Did you mean heat conduction?"), instead of answering
        -> build_grounding (refuse if not covered)
        -> LLM (grounded; may also refuse)
        -> Verifier (keep only sentences an approved passage supports)
        -> for an explanation, one example from village life, written in a second call
           and checked under its own rule (see _example), built from the teacher's own
           everyday examples when a file of them is installed (see teacher_examples.py)
        -> if that refused, one more try on re-ranked passages (see rerank.rescue)
        -> MT into target language
    => AnswerResult (text + citations + timings [+ audio if a TTS client is given])

No step touches the network. The LLM, embedder, and index all sit on local
storage. Every sentence spoken is supported by a DIKSHA passage, and that passage
is what gets cited.
"""

from __future__ import annotations

import logging
import queue
import re
import threading
import time
from dataclasses import dataclass, field
from typing import Callable, Iterator, List, Optional

from ..models.embed import DEFAULT_MODEL_NAME, Embed
from ..models.verify import Verifier
from .grounding import (
    DEFAULT_MIN_SCORE,
    DID_YOU_MEAN_TEXT,
    EXAMPLE_INTENTS,
    EXAMPLE_SYSTEM,
    EVERYDAY_WORDS,
    TEACHER_EXAMPLE_SYSTEM,
    _content_terms,
    REFUSAL_TEXT,
    UNCLEAR_TEXT,
    build_grounding,
    classify_intent,
    clean_answer,
    closest_term,
    example_prompt,
    is_example,
    teacher_example_prompt,
    is_refusal_response,
    iter_sentences,
    load_index_words,
    topic_phrase,
    unknown_words,
)
from .answer_guard import QUESTION_SIMILARITY, contradicts, unseen_name, wrong_number
from .rerank import CANDIDATES, load_vocabulary, rescue
from .safety import SafetyCheck, load_safety_model
from .teacher_examples import load_cached, pick
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

_UNSUPPORTED_REASON = "No sentence of the answer was supported by the approved passages."
# An example may not be contradicted by the passage it illustrates with this probability or more.
EXAMPLE_CONTRADICTION = 0.5
# A good example is a case of the fact before it, so it must entail that fact with this
# probability. On 16 examples Qwen wrote (11 November), this kept 6 of 9 good ones and
# dropped 6 of 7 that invented facts, such as a chulha with a concave mirror.
EXAMPLE_ILLUSTRATES = 0.5
# An example may use at most this many words that are not everyday village words and not
# already in the facts, question or passages. Examples that invented facts used 7 to 11 (a
# hand pump "powered by sunlight", a wind-driven "electromagnet"), and 12 of 13 sound ones
# used 6 or fewer, on 19 examples from the 60-question benchmark (11 November).
EXAMPLE_NEW_WORDS = 6
# An example built from a teacher's line must say what happens in it. Comparisons, and claims
# that the line "applies" or "demonstrates" a fact, were the ones that passed the check with a
# wrong fact (11 November): "pulling a bucket up from the well requires energy, just like
# plants need oxygen to produce energy through photosynthesis", and "putting wet clothes on a
# line to dry is a practical application of the fact that water is essential for ... digestion".
_COMPARISON = re.compile(r"\b(?:just like|is like|are like|like how|similar to|the same way|application of"
                         r"|demonstrat\w*|illustrat\w*|is an example of)\b", re.IGNORECASE)
_NONE = re.compile(r"^\W*none\b", re.IGNORECASE)


def _with_lead(reply: str) -> str:
    """A teacher example often comes back as a plain sentence about the teacher's line, as in
    "A steel ladle left in hot kheer gets hot because heat moves from the hot kheer to the
    steel ladle", so it gets the "For example," the check looks for. NONE stays as it is."""
    text = clean_answer(reply or "")
    if not text or is_example(text) or _NONE.match(text) or is_refusal_response(text):
        return reply
    return "For example, " + text[0].lower() + text[1:]


_EXAMPLE_LEAD = re.compile(r"^\W*(for example|for instance|e\.g\.)\W*", re.IGNORECASE)
_LIVE_REASON = "Asks for live information, which textbooks cannot give."

# Textbooks cannot say what is true today, and a maths problem about the price of gold
# would otherwise be read out as today's price. "Current" alone is left out, since
# electric current is a textbook topic.
_LIVE = re.compile(
    r"\b(today|today's|tonight|tomorrow|yesterday|right now|currently|at the moment|"
    r"latest|this (?:week|month|year))\b",
    re.IGNORECASE,
)


def asks_for_live_information(request: str) -> bool:
    return bool(_LIVE.search(request or ""))


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
    dropped_sentences: List[str] = field(default_factory=list)  # failed verification

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
    citations: List[str] = field(default_factory=list)   # sources not yet cited in this answer
    reason: str = ""
    timings: dict = field(default_factory=dict)          # each step, on the first sentence only
    example: bool = False                                # the model's own example, not a passage's fact

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
        verifier: Optional[Verifier] = None,
        safety_model: Optional[Verifier] = None,
        teacher_examples: Optional[str] = None,
    ):
        self.index = VectorIndex(index_dir)
        self.vocabulary = load_vocabulary(index_dir)
        self.words = load_index_words(index_dir, [c.get("text", "") for c in self.index.chunks])
        self.verifier = verifier or Verifier()
        self.safety = SafetyCheck(safety_model or load_safety_model())
        self.embedder = embedder or Embed(model_name=self.index.embedder_name or DEFAULT_MODEL_NAME)
        self._check_embedder_matches_index()
        self.use_teacher_examples(teacher_examples)
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
        return self._ground(request, self._search(request)[:self.top_k])

    def use_teacher_examples(self, path: Optional[str] = None):
        """Load the teacher's everyday examples (see teacher_examples.py) from path, or the
        default place. The checked lines and their vectors are cached (teacher_examples.load_cached).
        With none, examples come from the village prompt alone, as before."""
        loaded = load_cached(lambda lines: self.embedder.embed(lines, kind="passage"),
                             getattr(self.embedder, "model_name", None) or type(self.embedder).__name__, path,
                             flagged=self.safety.check_line)
        self.teacher_examples, self._teacher_vectors = loaded.lines, loaded.vectors
        # The teacher's words and names are the children's own experience, so an example may
        # use them freely.
        self._everyday_words = EVERYDAY_WORDS | _content_terms(" ".join(self.teacher_examples))
        self._teacher_names = loaded.names

    def _teacher_picks(self, request: str, kept: List[str]) -> List[str]:
        if not self.teacher_examples:
            return []
        query = self.embedder.embed_one(" ".join([request, *kept]), kind="query")
        return pick(query, self._teacher_vectors, self.teacher_examples)

    def _supporting_passage(self, request: str, sentence: str, passages: List[Retrieved], first: bool):
        """The passage behind a sentence, or None if the sentence should be dropped.

        Only an answer's first sentence is held to the question. Later sentences add detail
        and often say "they" or "this", so they would look unrelated on their own.
        """
        source = self.verifier.supporting_passage(sentence, passages)
        if source is None or contradicts(sentence, [p.text for p in passages]):
            return None
        if first:
            question = self.embedder.embed_one(request, kind="query")
            if float(question @ self.embedder.embed_one(sentence, kind="passage")) < QUESTION_SIMILARITY:
                return None
        return source

    def _example(self, request: str, intent: str, kept: List[str], sources: List[Retrieved],
                 passages: List[Retrieved]):
        """One example from village life for an explanation, as (sentence, passage), or None.

        The answer model writes it in a second, separate call, from the sentences that
        passed the check. If the teacher has installed a file of everyday examples, the
        model is first shown the few closest to the topic (teacher_examples.MARGIN) and
        asked to turn one into an example. If it says none fits, writes a comparison rather
        than a case, or what it writes fails the check, the village prompt is tried, so a
        teacher's file never leaves a class with fewer examples than before. That costs one
        more short call, only when the teacher's lines did not give an example.

        The example is the model's own, so no passage supports it word for word, and it has
        its own check. It is kept only if it adds no number, adds no name other than one the
        teacher wrote in their lines (such as Holi or the haat's village), flips no fact, brings in
        little new content (EXAMPLE_NEW_WORDS, where the teacher's words count as everyday
        ones), is close to the question, passes the safety check, and is a case of one of
        the kept sentences (it entails that sentence) without the passage behind that
        sentence contradicting it. It is cited to that passage.

        This cannot catch every invented detail: "in a village, you might find camels and
        alpacas providing wool" still entails "fibres come from animals". That is the cost
        of giving examples at all.
        """
        if intent not in EXAMPLE_INTENTS or not kept or self.llm is extractive_llm:
            return None
        if any(is_example(k) for k in kept):   # the passages already gave one
            return None
        prompts = []
        picked = self._teacher_picks(request, kept)
        if picked:
            prompts.append((TEACHER_EXAMPLE_SYSTEM, teacher_example_prompt(request, kept, picked)))
        prompts.append((EXAMPLE_SYSTEM, example_prompt(request, kept)))
        for system, user in prompts:
            try:
                reply = self.llm(system, user)
            except Exception as e:  # noqa: BLE001
                logger.warning("example failed (%s)", e)
                return None
            if system is TEACHER_EXAMPLE_SYSTEM:
                if _COMPARISON.search(reply or ""):
                    logger.info("Dropped a teacher example that only compares: %s", reply)
                    continue
                reply = _with_lead(reply)
            found = self._checked_example(reply, request, kept, sources, passages)
            if found is not None:
                return found
            if system is TEACHER_EXAMPLE_SYSTEM:
                logger.info("No teacher example fitted (%s), asking for a village one", (reply or "").strip()[:200])
        return None

    def _new_content(self, sentence: str, known: str):
        """Words of an example that are not in the known text, everyday village words or the teacher's."""
        return _content_terms(sentence) - _content_terms(known) - self._everyday_words

    def _checked_example(self, reply: str, request: str, kept: List[str], sources: List[Retrieved],
                         passages: List[Retrieved]):
        sentence = next(iter_sentences([clean_answer(reply or "")]), "")
        if not is_example(sentence) or is_refusal_response(sentence):
            return None
        texts = [p.text for p in passages]
        joined = " ".join(texts)
        names = getattr(self, "_teacher_names", ())
        if (wrong_number(sentence, joined) or unseen_name(sentence, joined, names)
                or contradicts(sentence, texts, names)):
            logger.info("Dropped an example that adds or flips a fact: %s", sentence)
            return None
        new = self._new_content(sentence, " ".join([request, *kept, joined]))
        if len(new) > EXAMPLE_NEW_WORDS:
            logger.info("Dropped an example that brings in too much new content %s: %s", sorted(new), sentence)
            return None
        body = _EXAMPLE_LEAD.sub("", sentence)
        question = self.embedder.embed_one(request, kind="query")
        if float(question @ self.embedder.embed_one(body, kind="passage")) < QUESTION_SIMILARITY:
            logger.info("Dropped an example far from the question: %s", sentence)
            return None
        if self.safety.check(sentence):
            return None
        for fact, source in zip(kept, sources):
            if (self.verifier.support_score(body, fact) >= EXAMPLE_ILLUSTRATES
                    and self.verifier.contradiction_score(source.text, sentence) < EXAMPLE_CONTRADICTION):
                return sentence, source
        logger.info("Dropped an example that shows none of the facts: %s", sentence)
        return None

    def _search(self, request: str, grade=None, own_first: bool = True) -> List[Retrieved]:
        """The top candidates in search order: the first top_k are the plain result,
        and the rest are kept for a rescue. With ``grade`` (a class, such as 6), only
        that class's passages, unless none of them would pass the retrieval gate, and
        books for that class alone first unless ``own_first`` is False."""
        qvec = self.embedder.embed_one(request, kind="query")
        if grade is None:
            return self.index.search(qvec, top_k=max(CANDIDATES, self.top_k))
        gate = DEFAULT_MIN_SCORE if self.min_score is None else self.min_score
        extra = {} if own_first else {"own_first": False}
        return self.index.search(qvec, top_k=max(CANDIDATES, self.top_k), grade=grade, min_score=gate,
                                 accept=lambda found: not self._ground(request, found[:self.top_k]).refused,
                                 **extra)

    def _ground(self, request: str, passages: List[Retrieved]):
        kwargs = {} if self.min_score is None else {"min_score": self.min_score}
        return build_grounding(request, passages, **kwargs)

    def _unclear(self, request: str, candidates: List[Retrieved]):
        """(word, phrase) when a topic word of the request is nowhere in the index, or None.

        ``phrase`` is the request's topic with the closest textbook word in place of the unknown
        one ("heat conduction" for "heat endaction"), or None when no word is close (see
        grounding.closest_term). With no close word, a request the gate would refuse anyway,
        such as "What is Bitcoin?", is left to the normal refusal. A real word the index has,
        such as "cricket", is never unknown, so an off-topic question about it is refused as
        before.
        """
        words = getattr(self, "words", None)
        unknown = unknown_words(request, words) if words else []
        if not unknown:
            return None
        others = _content_terms(request) - _content_terms(" ".join(unknown))
        for word in unknown:
            term = closest_term(word, others, self.vocabulary or words,
                                (c.get("text", "") for c in self.index.chunks), words)
            if term:
                return word, topic_phrase(request, word, term)
        if self._ground(request, candidates[:self.top_k]).refused:
            return None
        return unknown[0], None

    def _clarification(self, word: str, phrase: Optional[str], target_language: str):
        """What to say instead of an answer: (English, spoken text, reason)."""
        lang = (target_language or "en").lower()
        texts = DID_YOU_MEAN_TEXT if phrase else UNCLEAR_TEXT
        english = texts["en"].format(term=phrase)
        if lang == "hi":
            term = self._localize_text(phrase, "hi").strip(" ।.?") if phrase else None
            spoken = texts["hi"].format(term=term or phrase)
        else:
            spoken = self._localize_text(english, target_language)
        reason = f"unclear word: {word!r}" + (f", offered {phrase!r}" if phrase else "")
        return english, spoken, reason

    def stream_answer(self, request: str, target_language: str = "en",
                      grade=None) -> Iterator[SentenceResult]:
        """Yield the answer one sentence at a time (embed -> retrieve -> ground ->
        stream LLM -> per-sentence MT + TTS).

        Each yielded SentenceResult carries its synthesized audio, so a caller can
        play sentence N while this generator produces N+1 (see ``speak_stream``).
        Uses the streaming LLM if one was provided; otherwise it pseudo-streams by
        sentence-splitting a single full answer. A sentence no passage supports is
        dropped before MT and TTS. Refusals (retrieval gate, the LLM leading with the
        sentinel, or no sentence surviving) yield a single refused SentenceResult.
        """
        def _localize(text: str) -> str:
            return self._localize_text(text, target_language)

        def _audio(text: str) -> Optional[bytes]:
            return self._synthesize(text, target_language)

        steps = {}
        clock = [time.time()]

        def _mark(step: str):
            now = time.time()
            steps[step] = steps.get(step, 0.0) + now - clock[0]
            clock[0] = now

        def _refusal(reason: str) -> SentenceResult:
            loc = _localize(REFUSAL_TEXT)
            _mark("mt")
            audio = _audio(loc)
            _mark("tts")
            return SentenceResult(0, REFUSAL_TEXT, loc, audio, refused=True, reason=reason, timings=steps)

        flag = self.safety.check(request)
        _mark("safety")
        if flag:
            loc = self._localize_flag(flag, target_language)
            audio = _audio(loc)
            _mark("tts")
            yield SentenceResult(0, flag.response_en, loc, audio, refused=True,
                                 reason=f"safety: {flag.category} ({flag.source})", timings=steps)
            return

        if asks_for_live_information(request):
            yield _refusal(_LIVE_REASON)
            return

        def _attempt(passages):
            """Yield the supported sentences; return a refusal reason if there were none.
            Nothing is yielded before the first supported sentence, so a refused attempt
            can be retried without the listener hearing anything."""
            g = self._ground(request, passages)
            _mark("retrieve")
            if g.refused:
                return g.reason
            if self.stream_llm is not None:
                chunks = self.stream_llm(g.system_prompt, g.user_prompt)
            else:
                chunks = iter([self.llm(g.system_prompt, g.user_prompt)])
            idx = 0
            cited = set()
            kept, sources = [], []
            for i, sentence in enumerate(iter_sentences(chunks)):
                _mark("llm")
                if i == 0 and is_refusal_response(sentence):
                    return "LLM reported no answer in source"
                text = clean_answer(sentence)
                if not text:
                    continue
                source = self._supporting_passage(request, text, g.passages, first=idx == 0)
                _mark("check")
                if source is None:
                    logger.info("Dropped an unsupported sentence: %s", text)
                    continue
                kept.append(text)
                sources.append(source)
                citation = source.citation()
                new_citations = [] if citation in cited else [citation]
                cited.add(citation)
                loc = _localize(text)
                _mark("mt")
                audio = _audio(loc)
                _mark("tts")
                yield SentenceResult(
                    index=idx, text_en=text, text_localized=loc, audio=audio,
                    citations=new_citations, timings=dict(steps) if idx == 0 else {},
                )
                clock[0] = time.time()
                idx += 1
            if not idx:
                return _UNSUPPORTED_REASON
            example = self._example(request, g.intent, kept, sources, g.passages)
            if example is not None:
                text, source = example
                loc = _localize(text)
                audio = _audio(loc)
                citation = source.citation()
                yield SentenceResult(index=idx, text_en=text, text_localized=loc, audio=audio,
                                     citations=[] if citation in cited else [citation], example=True)
            return None

        candidates = self._search(request, grade)
        unclear = self._unclear(request, candidates)
        if unclear:
            english, spoken, reason = self._clarification(*unclear, target_language)
            _mark("mt")
            audio = _audio(spoken)
            _mark("tts")
            yield SentenceResult(0, english, spoken, audio, refused=True, reason=reason, timings=steps)
            return
        reason = yield from _attempt(candidates[:self.top_k])
        if reason is not None:
            retry = rescue(request, candidates, self.top_k, self.vocabulary)
            if retry is not None:
                logger.info("Retrying on re-ranked passages after: %s", reason)
                reason = yield from _attempt(retry)
        if reason is not None and grade is not None:
            # The class's own books passed the gate but gave no answer, as "nutrition in
            # plants" did for class 6. Books it shares with other classes get one try.
            wider = self._search(request, grade, own_first=False)[:self.top_k]
            if [(p.content_id, p.page, p.text) for p in wider] != [
                    (p.content_id, p.page, p.text) for p in candidates[:self.top_k]]:
                logger.info("Retrying with books shared with other classes after: %s", reason)
                reason = yield from _attempt(wider)
        if reason is not None:
            yield _refusal(reason)

    def _localize_text(self, text: str, target_language: str) -> str:
        if self.mt and target_language and target_language.lower() != "en":
            try:
                return self.mt(text, "EN", target_language)
            except Exception as e:  # noqa: BLE001
                logger.warning("stream MT failed (%s)", e)
        return text

    def _synthesize(self, text: str, target_language: str) -> Optional[bytes]:
        if self.tts:
            try:
                return self.tts(text, target_language)
            except Exception as e:  # noqa: BLE001
                logger.warning("stream TTS failed (%s)", e)
        return None

    def say(self, text_en: str, target_language: str = "en", index: int = 0) -> SentenceResult:
        """One of the device's own fixed sentences, translated and spoken like an answer."""
        loc = self._localize_text(text_en, target_language)
        return SentenceResult(index, text_en, loc, self._synthesize(loc, target_language))

    def _localize_flag(self, flag, target_language: str) -> str:
        """Use the human-written text where there is one; translate only as a fallback."""
        lang = (target_language or "en").lower()
        if lang == "en":
            return flag.response_en
        if lang == "hi":
            return flag.response_hi
        if self.mt:
            try:
                return self.mt(flag.response_en, "EN", target_language)
            except Exception as e:  # noqa: BLE001
                logger.warning("MT failed on a safety response (%s)", e)
        return flag.response_en

    def answer(self, request: str, target_language: str = "en", grade=None) -> AnswerResult:
        t = {}
        t0 = time.time()

        flag = self.safety.check(request)
        t["safety"] = time.time() - t0
        if flag:
            result = AnswerResult(
                refused=True, intent="safety", request=request, target_language=target_language,
                answer_en=flag.response_en,
                answer_localized=self._localize_flag(flag, target_language),
                reason=f"safety: {flag.category} ({flag.source})", timings=t,
            )
            self._maybe_tts(result)
            t["total"] = time.time() - t0
            return result

        if asks_for_live_information(request):
            result = self._finalize_refusal(request, target_language, classify_intent(request),
                                            _LIVE_REASON, [], t)
            t["total"] = time.time() - t0
            return result

        ts = time.time()
        candidates = self._search(request, grade)
        t["retrieve"] = time.time() - ts
        unclear = self._unclear(request, candidates)
        if unclear:
            english, spoken, reason = self._clarification(*unclear, target_language)
            result = AnswerResult(refused=True, intent=classify_intent(request), request=request,
                                  target_language=target_language, answer_en=english, answer_localized=spoken,
                                  reason=reason, passages=candidates[:self.top_k], timings=t)
            self._maybe_tts(result)
            t["total"] = time.time() - t0
            return result
        passages = candidates[:self.top_k]
        g, kept, citations, dropped = self._try_passages(request, passages, t)
        if not kept:
            retry = rescue(request, candidates, self.top_k, self.vocabulary)
            if retry is not None:
                logger.info("Retrying on re-ranked passages after: %s", g.reason)
                passages = retry
                g, kept, citations, dropped = self._try_passages(request, passages, t)
        if not kept:
            result = self._finalize_refusal(request, target_language, g.intent, g.reason, passages, t)
            result.dropped_sentences = dropped
            t["total"] = time.time() - t0
            return result

        llm_text = " ".join(kept)
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
            citations=citations,
            passages=g.passages,  # relevance-filtered set that was actually grounded on
            timings=t,
            dropped_sentences=dropped,
        )
        self._maybe_tts(result)
        t["total"] = time.time() - t0
        return result

    def _try_passages(self, request: str, passages: List[Retrieved], t: dict):
        """Ground, generate and check one set of passages. Returns the grounding, the
        supported sentences, their citations and the dropped sentences. No supported
        sentences means a refusal, with the reason in the grounding."""
        g = self._ground(request, passages)
        if g.refused:
            return g, [], [], []

        ts = time.time()
        llm_text = self.llm(g.system_prompt, g.user_prompt).strip()
        t["llm"] = t.get("llm", 0.0) + time.time() - ts

        # Generation gate: the model says the sources don't cover it.
        if is_refusal_response(llm_text):
            snippet = (llm_text or "").strip()[:100] or "(empty response)"
            g.reason = f"LLM reported insufficient information (said: {snippet!r})."
            return g, [], [], []

        # Keep only what an approved passage supports, and cite only those passages.
        ts = time.time()
        kept, citations, dropped, sources = [], [], [], []
        for sentence in iter_sentences([llm_text]):
            # Strip any leaked refusal note the model appended to a valid answer.
            text = clean_answer(sentence)
            if not text:
                continue
            source = self._supporting_passage(request, text, g.passages, first=not kept)
            if source is None:
                dropped.append(text)
                continue
            kept.append(text)
            sources.append(source)
            if source.citation() not in citations:
                citations.append(source.citation())
        if kept:
            example = self._example(request, g.intent, kept, sources, g.passages)
            if example is not None:
                kept.append(example[0])
                if example[1].citation() not in citations:
                    citations.append(example[1].citation())
        t["verify"] = t.get("verify", 0.0) + time.time() - ts
        if not kept:
            g.reason = _UNSUPPORTED_REASON
        return g, kept, citations, dropped

    def _finalize_refusal(self, request, target_language, intent, reason, passages, t) -> AnswerResult:
        localized = REFUSAL_TEXT
        if self.mt and target_language and target_language.lower() != "en":
            try:
                localized = self.mt(REFUSAL_TEXT, "EN", target_language)
            except Exception:  # noqa: BLE001
                pass
        result = AnswerResult(
            refused=True,
            intent=intent,
            request=request,
            target_language=target_language,
            answer_en=REFUSAL_TEXT,
            answer_localized=localized,
            reason=reason,
            passages=passages,
            timings=t,
        )
        self._maybe_tts(result)
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
    LLM client (see llm.py) instead.
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
