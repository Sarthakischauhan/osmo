"""Reusable harness executor for local and application-owned worker backends."""

from __future__ import annotations

import logging

from core_harness import CoreHarness, EventSink, HarnessCancelled, HarnessLimitExceeded

from core_server.config import ServerConfig
from core_server.models import RunSubmission

logger = logging.getLogger(__name__)


class RunExecutor:
    """Build and execute one harness run using application-owned configuration.

    Queue workers can construct this with their local ``ServerConfig`` and call
    ``execute`` for a stored ``RunSubmission``, passing an event sink backed by
    the application's shared event store.
    """

    def __init__(self, config: ServerConfig) -> None:
        self.config = config

    async def execute(
        self,
        run_id: str,
        submission: RunSubmission,
        sink: EventSink,
    ) -> None:
        run = submission.request
        context = submission.context
        harness = CoreHarness(
            registry=self.config.registry,
            model_id=run.model_id or self.config.model_id,
            system_prompt=self.config.system_prompt,
            config=self.config.to_harness_config(),
            reasoning_effort=run.reasoning_effort or self.config.reasoning_effort,
            tools=list(self.config.tools),
            sink=sink,
            session_id=context.session_id,
            agent_id=run_id,
            addons=self.config.addons_for_run(context) or None,
        )
        try:
            await harness.run(
                run.message,
                conversation=run.conversation,
                session_id=context.session_id,
            )
        except HarnessCancelled:
            logger.info("Harness run %s cancelled", run_id)
        except HarnessLimitExceeded as exc:
            logger.warning("Harness run %s limit exceeded: %s", run_id, exc)
        except Exception:
            # The harness emits the terminal failure event before propagating.
            logger.exception("Harness run %s failed", run_id)


__all__ = ["RunExecutor"]
