from __future__ import annotations

from types import SimpleNamespace

from homedatabase_api.services import reports as report_service


def test_postgres_report_uses_a_dedicated_repeatable_read_session(monkeypatch):
    events = []
    report_member = SimpleNamespace(id="m-son", is_admin=True)

    class FakeConnection:
        def __enter__(self):
            events.append("connection-enter")
            return self

        def __exit__(self, *_args):
            events.append("connection-exit")

        def execution_options(self, **options):
            events.append(("isolation", options["isolation_level"]))
            return self

    class FakeEngine:
        def connect(self):
            events.append("connect")
            return FakeConnection()

    class FakeBind:
        dialect = SimpleNamespace(name="postgresql")
        engine = FakeEngine()

    class FakeOuterSession:
        def get_bind(self):
            return FakeBind()

    class FakeReportSession:
        def __init__(self, *, bind, autoflush, expire_on_commit):
            assert isinstance(bind, FakeConnection)
            assert autoflush is False
            assert expire_on_commit is False

        def __enter__(self):
            events.append("session-enter")
            return self

        def __exit__(self, *_args):
            events.append("session-exit")

        def get(self, model, member_id):
            assert model is report_service.Member
            assert member_id == "m-son"
            return report_member

    monkeypatch.setattr(report_service, "Session", FakeReportSession)
    with report_service._report_read_session(FakeOuterSession(), SimpleNamespace(id="m-son")) as (
        session,
        member,
    ):
        assert isinstance(session, FakeReportSession)
        assert member is report_member
        events.append("yielded")

    assert events == [
        "connect",
        "connection-enter",
        ("isolation", "REPEATABLE READ"),
        "session-enter",
        "yielded",
        "session-exit",
        "connection-exit",
    ]
