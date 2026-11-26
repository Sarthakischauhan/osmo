"""Native requests stay on one voice socket; consent loss cancels outstanding work."""
from __future__ import annotations
import asyncio
import json
from typing import Any
from unittest.mock import AsyncMock
import pytest
from core_ai.types import StreamEvent
from conftest import FakeGrok, make_client, xai_key_store
from test_session import call, next_event, say, start, tool_output
from symphony_server.desktop import ComputerTool, DesktopRunLink, DesktopSession, RUN_DESKTOP
from symphony_server.protocol import DesktopResult
from symphony_server.settings import Settings

async def test_revocation_cancels_pending_requests_and_ignores_late_replies() -> None:
    emitted = asyncio.Queue()
    desktop = DesktopSession(emitted.put)
    with pytest.raises(RuntimeError, match='Enable computer support'):
        await desktop.request({'action': 'screenshot'})
    desktop.configure(True)
    task = asyncio.create_task(desktop.request({'action': 'screenshot'}))
    event = await emitted.get()
    desktop.resolve(DesktopResult(type='desktop.result', requestId='unrelated', result={'ok': True}))
    assert not task.done()
    desktop.configure(False)
    with pytest.raises(RuntimeError, match='stopped'):
        await task
    desktop.resolve(DesktopResult(type='desktop.result', requestId=event.requestId, result={'image': 'late'}))
    assert not desktop.pending

async def test_cancelled_tool_leaves_no_pending_request() -> None:
    emitted = asyncio.Queue()
    desktop = DesktopSession(emitted.put)
    desktop.configure(True)
    task = asyncio.create_task(desktop.request({'action': 'screenshot'}))
    await emitted.get()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert not desktop.pending

async def test_concurrent_runs_keep_desktops_isolated() -> None:
    async def check(desktop: DesktopSession) -> None:
        await DesktopRunLink(desktop).before_run()
        await asyncio.sleep(0)
        assert RUN_DESKTOP.get() is desktop
    await asyncio.gather(check(DesktopSession(AsyncMock())), check(DesktopSession(AsyncMock())))
    assert RUN_DESKTOP.get() is None

async def test_observation_uses_vision_and_keeps_images_out_of_voice_history() -> None:
    class Registry:
        messages: Any = None
        async def stream(self, **kwargs: Any) -> Any:
            self.messages = kwargs['messages']
            assert kwargs['model_id'] == 'grok:vision-test'
            yield StreamEvent(type='text_delta', delta='Settings is at (0.8, 0.2).')
    class Access:
        registry = Registry()
        async def prepare(self, provider: str) -> bool:
            assert provider == 'xai'
            return True
    access = Access()
    desktop: DesktopSession
    async def emit(event: Any) -> None:
        desktop.resolve(DesktopResult(type='desktop.result', requestId=event.requestId,
            result={'image': 'abc123', 'width': 1440, 'height': 900}))
    desktop = DesktopSession(emit)
    desktop.configure(True)
    token = RUN_DESKTOP.set(desktop)
    try:
        tool = ComputerTool(access, 'grok:vision-test')  # type: ignore[arg-type]
        result = json.loads(await tool.execute(sink=None, args={'action': 'observe', 'question': 'Where are settings?'}))
        assert result == {'observation': 'Settings is at (0.8, 0.2).', 'width': 1440, 'height': 900}
        assert 'abc123' not in json.dumps(result)
        assert access.registry.messages[1].content[1] == {'type': 'image', 'media_type': 'image/jpeg', 'data': 'abc123'}
        assert 'Missing required' in json.loads(await tool.execute(sink=None, args={'action': 'click'}))['error']
    finally:
        RUN_DESKTOP.reset(token)

def agent_with_desktop(grok: FakeGrok, steps: list[Any]) -> Any:
    from core_ai import ModelRegistry
    from test_agents import ScriptedAgent
    registry = ModelRegistry()
    settings = Settings(xai_base_url=grok.base_url, tool_categories=('desktop',))
    client = make_client(settings, secret_store=xai_key_store(), registry=registry)
    registry.register('grok', ScriptedAgent(steps))
    return client

def test_the_computer_is_the_agents_not_the_voice_models() -> None:
    from symphony_server.app import AGENT_TOOLS, VOICE_TOOLS
    assert 'computer' in AGENT_TOOLS and 'computer' not in VOICE_TOOLS

def test_an_agent_run_operates_the_desktop_of_its_voice_socket_until_revoked(grok: FakeGrok) -> None:
    from test_agents import Step
    steps = [
        Step(tool='computer', arguments=json.dumps({'action': 'click', 'x': 0.8, 'y': 0.2})),
        Step(tool='computer', arguments=json.dumps({'action': 'key', 'keys': ['enter']})),
        Step(text='Clicked settings; computer support was turned off before Enter.'),
    ]
    with agent_with_desktop(grok, steps) as client, client.websocket_connect('/ws') as ws:
        update = start(ws, grok)
        assert 'computer' not in {tool['name'] for tool in update['session']['tools']}
        ws.send_json({'type': 'desktop.configure', 'enabled': True})
        say(grok, 'Click settings and press enter')
        call(grok, 'run_agent', {'task': 'Click settings, then press Enter'}, 'delegate')
        started = tool_output(grok)
        assert started['status'] == 'running'

        request = next_event(ws, 'desktop.request')
        assert request['action'] == {'action': 'click', 'x': 0.8, 'y': 0.2, 'button': 'left'}
        # Only the socket that started the run can answer for its desktop.
        with client.websocket_connect('/ws') as other:
            other.send_json({'type': 'desktop.result', 'requestId': request['requestId'], 'result': {'ok': 'wrong socket'}})
        ws.send_json({'type': 'desktop.result', 'requestId': request['requestId'], 'result': {'ok': True}})

        # Turning computer support off stops the request still waiting; the run goes on and finishes.
        next_event(ws, 'desktop.request')
        ws.send_json({'type': 'desktop.configure', 'enabled': False})
        done = next_event(ws, 'agent.update')
        while done['status'] != 'completed':
            done = next_event(ws, 'agent.update')
        assert done['runId'] == started['run_id']
        assert done['result'] == 'Clicked settings; computer support was turned off before Enter.'

def test_without_computer_support_the_agents_computer_call_is_refused(grok: FakeGrok) -> None:
    from core_ai import ModelRegistry
    from test_agents import ScriptedAgent, Step

    class Recording(ScriptedAgent):
        """Keeps what the model was sent, so the test sees the tool result it got."""
        seen: list[Any] = []

        async def stream(self, model_name: str, messages: list[Any], *args: Any, **kwargs: Any) -> Any:
            self.seen.append(str(messages))
            async for event in super().stream(model_name, messages, *args, **kwargs):
                yield event

    agent = Recording([Step(tool='computer', arguments=json.dumps({'action': 'observe'})), Step(text='Computer support is off.')])
    registry = ModelRegistry()
    client = make_client(Settings(xai_base_url=grok.base_url, tool_categories=('desktop',)), secret_store=xai_key_store(), registry=registry)
    registry.register('grok', agent)
    with client, client.websocket_connect('/ws') as ws:
        start(ws, grok)
        say(grok, 'What is on my screen?')
        call(grok, 'run_agent', {'task': 'Describe the screen'}, 'delegate')
        assert tool_output(grok)['status'] == 'running'
        done = next_event(ws, 'agent.update')
        while done['status'] != 'completed':
            done = next_event(ws, 'agent.update')
        assert done['result'] == 'Computer support is off.'
        assert 'Enable computer support in the Osmo desktop app first' in agent.seen[-1]
