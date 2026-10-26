"""Native requests stay on one voice socket; consent loss cancels outstanding work."""
from __future__ import annotations
import asyncio
import json
from typing import Any
from unittest.mock import AsyncMock
import pytest
from core_ai.types import StreamEvent
from conftest import FakeGrok, make_client, xai_key_store
from test_session import answer, call, next_event, say, start, tool_output, wait_listening
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

def test_harness_computer_rpc_roundtrip_and_revocation(grok: FakeGrok) -> None:
    settings = Settings(xai_base_url=grok.base_url, tool_categories=('desktop',))
    with make_client(settings, secret_store=xai_key_store()) as client:
        with client.websocket_connect('/ws') as ws:
            update = start(ws, grok)
            assert 'computer' in {tool['name'] for tool in update['session']['tools']}
            ws.send_json({'type': 'desktop.configure', 'enabled': True})
            say(grok, 'Click settings')
            call(grok, 'computer', {'action': 'click', 'x': 0.8, 'y': 0.2}, 'c1')
            request = next_event(ws, 'desktop.request')
            assert request['action'] == {'action': 'click', 'x': 0.8, 'y': 0.2, 'button': 'left'}
            with client.websocket_connect('/ws') as other:
                other.send_json({'type': 'desktop.result', 'requestId': request['requestId'], 'result': {'ok': 'wrong socket'}})
            ws.send_json({'type': 'desktop.result', 'requestId': request['requestId'], 'result': {'ok': True}})
            assert tool_output(grok) == {'ok': True}
            answer(grok, 'Opened settings.', 'r2')
            wait_listening(ws)
            say(grok, 'Press enter')
            call(grok, 'computer', {'action': 'key', 'keys': ['enter']}, 'c2', 'r3')
            next_event(ws, 'desktop.request')
            ws.send_json({'type': 'desktop.configure', 'enabled': False})
            assert 'stopped' in tool_output(grok)['error']
            answer(grok, 'Computer support stopped.', 'r4')
            wait_listening(ws)

def test_delegated_agent_reaches_only_its_originating_desktop(grok: FakeGrok) -> None:
    from core_ai import ModelRegistry
    from test_agents import ScriptedAgent, Step
    registry = ModelRegistry()
    agent = ScriptedAgent([Step(tool='computer', arguments=json.dumps({'action': 'click', 'x': 0.4, 'y': 0.6})), Step(text='Clicked the control.')])
    settings = Settings(xai_base_url=grok.base_url, tool_categories=('desktop',))
    client = make_client(settings, secret_store=xai_key_store(), registry=registry)
    registry.register('grok', agent)
    with client, client.websocket_connect('/ws') as ws:
        start(ws, grok)
        ws.send_json({'type': 'desktop.configure', 'enabled': True})
        say(grok, 'Handle the computer task in the background')
        call(grok, 'run_agent', {'task': 'Click the control'}, 'delegate')
        started = tool_output(grok)
        assert started['status'] == 'running'
        request = next_event(ws, 'desktop.request')
        assert request['action']['x'] == 0.4
        ws.send_json({'type': 'desktop.result', 'requestId': request['requestId'], 'result': {'ok': True}})
        done = next_event(ws, 'agent.update')
        while done['status'] != 'completed':
            done = next_event(ws, 'agent.update')
        assert done['runId'] == started['run_id']
        assert done['result'] == 'Clicked the control.'
