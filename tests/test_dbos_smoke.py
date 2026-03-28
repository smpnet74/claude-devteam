"""Smoke test for DBOS test fixture."""

import pytest
from dbos import DBOS


@pytest.mark.asyncio
async def test_dbos_workflow_runs(dbos_launch):
    @DBOS.workflow()
    async def hello(name: str) -> str:
        return f"hello {name}"

    assert await hello("world") == "hello world"


@pytest.mark.asyncio
async def test_dbos_step_runs(dbos_launch):
    @DBOS.step()
    async def add(a: int, b: int) -> int:
        return a + b

    @DBOS.workflow()
    async def add_wf(a: int, b: int) -> int:
        return await add(a, b)

    assert await add_wf(3, 4) == 7
