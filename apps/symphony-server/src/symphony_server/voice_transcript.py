"""What a voice session keeps of its conversation: the thread's messages, the utterances still awaiting their
transcript, the answers held back until those arrive, and the Langfuse turn of each utterance.

Transcription finishes after the answer has often started, so each utterance's bubble is opened when speech stops
and filled in later; an answer is stored only once every utterance it replies to is, so the stored thread reads in
the order things were said.
"""

from __future__ import annotations

from typing import Any

from symphony_server import tracing
from symphony_server.storage import MessageRole, Storage
from symphony_server.voices import VoiceModel

# A thread is titled from its first utterance, cut to this length.
TITLE_FROM_SPEECH_MAX = 60


def conversation_title(text: str) -> str | None:
    """A short title from the first line of a conversation, or None when there is nothing to name it with."""
    line = " ".join(text.split())
    if not line:
        return None
    cut = line[:TITLE_FROM_SPEECH_MAX].rsplit(" ", 1)[0] if len(line) > TITLE_FROM_SPEECH_MAX and " " in line[:TITLE_FROM_SPEECH_MAX] else line[:TITLE_FROM_SPEECH_MAX]
    title = cut.strip(" \t.,;:!?-—–")
    return title or None


class Transcript:
    """The conversation of one voice session on its thread (`thread_id`; nothing is stored while it is None)."""

    def __init__(self, storage: Storage, instructions: str) -> None:
        self.storage, self.instructions = storage, instructions
        self.thread_id: str | None = None
        self.utterances = 0
        # Ended utterances whose transcript has not arrived, oldest first.
        self.unheard: list[str] = []
        # The open Langfuse turn, keyed by utterance id. Closed when its transcript is stored.
        self.traces: dict[str, Any] = {}
        # Answers held back from storage until the utterances they reply to are stored.
        self.held: list[tuple[frozenset[str], str]] = []

    def record(self, role: MessageRole, text: str) -> None:
        """Store one message on the thread."""
        if self.thread_id is not None:
            self.storage.add_message(self.thread_id, role, text)

    def next_utterance(self) -> str:
        """A new utterance id (the id of its bubble in the browser)."""
        self.utterances += 1
        return f"u{self.utterances}"

    def forget(self, utterance: str) -> bool:
        """Give up on an utterance's transcript; False when it had already arrived."""
        if utterance not in self.unheard:
            return False
        self.unheard.remove(utterance)
        self.store_held()
        return True

    def history(self) -> list[tuple[Any, str]]:
        """The thread so far, as (role, text) for a trace."""
        if self.thread_id is None:
            return []
        return [(message.role, message.text) for message in self.storage.messages(self.thread_id)]

    def open_trace(self, utterance: str, model: VoiceModel, voice: str) -> None:
        """Start the turn before its transcript exists. The transcript fills the input later."""
        if not tracing.enabled() or utterance in self.traces:
            return
        context = tracing.turn(
            session_id=self.thread_id,
            user_text="",
            instructions=self.instructions,
            history=self.history(),
            model=tracing.model_name(model),
            voice=voice,
        )
        span, generation = context.__enter__()
        self.traces[utterance] = (context, span, generation)

    def finish_trace(self, utterance: str, user_text: str) -> None:
        """Close an utterance's turn with what was said."""
        open_trace = self.traces.pop(utterance, None)
        if open_trace is None:
            return
        context, span, generation = open_trace
        if span is not None:
            span.update(input=user_text)
        if generation is not None:
            generation.update(input=tracing.messages(self.instructions, self.history(), user_text))
        context.__exit__(None, None, None)

    def note_answer(self, after: frozenset[str] | None, text: str) -> None:
        """The answer belongs to the utterance it replies to, which is still open."""
        utterance = next((item for item in (after or ()) if item in self.traces), None)
        if utterance is None:
            return
        _, span, generation = self.traces[utterance]
        if span is not None:
            span.update(output=text)
        if generation is not None:
            generation.update(output=text)

    def hold(self, after: frozenset[str], text: str) -> None:
        """An answer to store once the utterances in `after` are stored (now, when they all are)."""
        self.held.append((after, text))
        self.store_held()

    def store_held(self) -> None:
        """Store held answers, in order, once nothing they reply to is still unheard."""
        while self.held and not self.held[0][0].intersection(self.unheard):
            self.record("assistant", self.held.pop(0)[1])

    def flush_held(self) -> None:
        """The voice session is over: transcripts still owed will not come, so store the held answers now."""
        for _, text in self.held:
            self.record("assistant", text)
        self.held.clear()
