"""Provider compatibility for background coding tool declarations."""

import pytest
from pydantic import ValidationError

from symphony_server.upstream.sandbox.tools import ShellArgs


def test_shell_timeout_schema_is_gemini_compatible_and_positive() -> None:
    timeout = ShellArgs.model_json_schema()["properties"]["timeout_s"]
    assert "exclusiveMinimum" not in timeout
    assert timeout["minimum"] > 0
    with pytest.raises(ValidationError):
        ShellArgs(command="echo hello", timeout_s=0)
    assert ShellArgs(command="echo hello", timeout_s=1).timeout_s == 1
