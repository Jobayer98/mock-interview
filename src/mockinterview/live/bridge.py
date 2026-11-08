"""Connect the blocking step engine to asynchronous voice turns. Nothing here depends on LiveKit.

A candidate can interrupt at any moment, which cancels the voice framework's task. The engine must never be left halfway through
a step, so each step runs to completion on a worker thread while the voice side only reads what it produces.
"""
from __future__ import annotations

import asyncio
import time
from collections.abc import AsyncIterator, Callable
from statistics import median

from mockinterview.interview.engine import Finished, InterviewEngine, Say


class LiveInterview:
    def __init__(self, engine: InterviewEngine):
        self._engine = engine
        self._running: asyncio.Future | None = None
        self._last_transcript = ""
        self.result: dict | None = None

    def opening(self) -> list[Say]:
        """The lines before the candidate's first reply. These come from code and need no model call."""
        return [action for action in self._engine.start() if isinstance(action, Say)]

    def _pump(self, reply: str | None, emit: Callable[[object], None]) -> None:
        try:
            for action in self._engine.step(reply):
                if isinstance(action, Finished):
                    self.result = action.session
                emit(action)
        finally:
            emit(None)

    def _new_part(self, transcript: str) -> str:
        """If an interrupted turn is sent again with more words, only the new words are a new reply."""
        text = transcript.strip()
        previous, self._last_transcript = self._last_transcript, text
        return text[len(previous):].strip() if previous and text.startswith(previous) else text

    async def _finish_running_step(self) -> None:
        if self._running is not None:
            await asyncio.shield(self._running)

    def _start_step(self, reply: str | None) -> asyncio.Queue:
        loop = asyncio.get_running_loop()
        queue: asyncio.Queue = asyncio.Queue()

        def emit(action: object) -> None:
            try:
                loop.call_soon_threadsafe(queue.put_nowait, action)
            except RuntimeError:  # the event loop has closed; the engine still finishes its step
                pass

        self._running = loop.run_in_executor(None, self._pump, reply, emit)
        return queue

    async def reply(self, transcript: str) -> AsyncIterator[Say]:
        """Yield what the interviewer says, as each line becomes ready."""
        await self._finish_running_step()
        text = self._new_part(transcript)
        if self.result is not None or not text:
            return
        queue = self._start_step(text)
        while (action := await queue.get()) is not None:
            if isinstance(action, Say):
                yield action

    async def close(self) -> dict | None:
        """The candidate has gone: finish grading what they answered. Safe to call more than once."""
        await self._finish_running_step()
        if self.result is None and self._engine.state.phase != "new":
            queue = self._start_step(None)
            while await queue.get() is not None:
                pass
        return self.result


class TurnTimer:
    """Seconds from the end of the candidate's speech to the interviewer's first audio."""

    def __init__(self, clock: Callable[[], float] = time.monotonic):
        self._clock = clock
        self._speech_ended_at: float | None = None
        self.turns: list[float] = []

    def speech_ended(self) -> None:
        self._speech_ended_at = self._clock()

    def audio_started(self) -> None:
        if self._speech_ended_at is not None:
            self.turns.append(round(self._clock() - self._speech_ended_at, 3))
            self._speech_ended_at = None

    def summary(self) -> dict:
        return {"turns": len(self.turns), "median_s": median(self.turns) if self.turns else None, "max_s": max(self.turns, default=None), "all_s": self.turns}
