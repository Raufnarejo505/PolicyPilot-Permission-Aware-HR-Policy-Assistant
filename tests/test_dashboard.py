"""Exercise the landing page, role-based login, employee workspace, and admin portal."""

import json
import secrets
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import Mock

import pytest
from streamlit import config
from streamlit.proto.TextInput_pb2 import TextInput
from streamlit.testing.v1 import AppTest

from policypilot.app.auth_api import MeResponse
from policypilot.app.models.domain import Permission, SystemRole
from policypilot.ui.client import (
    AdminAccount,
    AdminOverview,
    DashboardError,
    PolicySummary,
    QueryAnswer,
    QueryCitation,
    SourceView,
)


def visible_output(app):
    # AppTest's own repr includes server-side session state, unlike rendered widgets.
    return "\n".join(str(node.proto) for node in app if hasattr(node, "proto"))


def run_dashboard():
    from policypilot.ui.dashboard import render

    render()


def employee_identity():
    return MeResponse(
        user_id="alice",
        content_permissions=[Permission.PUBLIC, Permission.EMPLOYEE],
        system_roles=[],
        permission_version=0,
    )


def admin_identity():
    return MeResponse(
        user_id="admin",
        content_permissions=[Permission.PUBLIC, Permission.EMPLOYEE],
        system_roles=[SystemRole.ADMIN],
        permission_version=2,
    )


def admin_overview():
    return AdminOverview(
        accounts=(
            AdminAccount(
                user_id="admin",
                username="admin",
                enabled=True,
                permissions=(),
                system_roles=("Admin",),
                permission_version=2,
            ),
            AdminAccount(
                user_id="alice",
                username="alice",
                enabled=True,
                permissions=("HR",),
                system_roles=(),
                permission_version=1,
            ),
        ),
        documents=18,
        chunks=656,
    )


def policy_summary():
    return PolicySummary(
        document_id="doc-leave",
        title="Annual Leave Policy",
        source_url="https://example.invalid/leave",
        version="v1",
        updated_at=datetime(2026, 10, 25, tzinfo=UTC),
        sections=("Eligibility", "Carry-over"),
        chunks=12,
        visible_via=("Public", "Employee"),
    )


@pytest.fixture
def dashboard(monkeypatch, tmp_path):
    client = Mock()
    client.health.return_value = True
    client.login.return_value = "synthetic-session-value"
    client.me.return_value = employee_identity()
    client.admin_overview.return_value = admin_overview()
    client.policies.return_value = (policy_summary(),)
    monkeypatch.setenv(
        "POLICYPILOT_UI_DEMO_CREDENTIALS", str(tmp_path / "absent-demo-credentials.json")
    )
    monkeypatch.setattr("policypilot.ui.dashboard.ApiClient", Mock(return_value=client))
    return AppTest.from_function(run_dashboard, default_timeout=10), client


def open_login(app, mode="employee"):
    app.run()
    app.button(key="landing_employee" if mode == "employee" else "landing_admin").click().run()
    assert not app.exception


def sign_in(app, username="alice", password: str | None = None):
    password = password or secrets.token_urlsafe(24)
    app.text_input(key="login_username").input(username)
    app.text_input(key="login_password").input(password)
    app.button(key="sign_in").click().run()


def test_landing_page_is_public_and_offers_role_entry_points(dashboard):
    app, client = dashboard
    app.run()
    assert not app.exception
    assert not app.text_input
    assert app.button(key="landing_employee")
    assert app.button(key="landing_admin")
    rendered = visible_output(app)
    assert "Policy answers," in rendered
    assert "scoped to your role" in rendered
    assert "Permission-aware HR policy assistant" in rendered
    assert "One question. Three roles." in rendered
    assert "approved policies" in rendered
    assert "indexed passages" in rendered
    assert "Trust is enforced, not promised." in rendered
    assert not client.login.called
    assert not client.me.called


def _write_demo_credentials(path, password, accounts):
    path.write_text(
        json.dumps({"version": 1, "password": password, "accounts": accounts}),
        encoding="utf-8",
    )
    path.chmod(0o600)


def test_demo_role_switcher_uses_real_login_and_never_displays_password(
    dashboard, monkeypatch, tmp_path
):
    app, client = dashboard
    password = secrets.token_urlsafe(24)
    credentials = tmp_path / "demo-accounts.json"
    _write_demo_credentials(
        credentials,
        password,
        [
            {
                "label": "Employee",
                "description": "Public + Employee baseline",
                "username": "demo.employee",
                "destination": "workspace",
            }
        ],
    )
    monkeypatch.setenv("POLICYPILOT_UI_DEMO_CREDENTIALS", str(credentials))
    app.run()
    assert not app.exception
    assert app.button(key="demo_login_demo.employee")
    rendered = visible_output(app)
    assert "Choose a demo role." in rendered
    assert "Three core roles" in rendered
    assert password not in rendered
    app.button(key="demo_login_demo.employee").click().run()
    assert not app.exception
    client.login.assert_called_once_with("demo.employee", password)
    assert app.session_state["page"] == "workspace"
    assert app.session_state["demo_persona"] == "Employee"
    assert app.button(key="switch_demo")
    assert password not in visible_output(app)


def test_demo_admin_destination_opens_admin_console(dashboard, monkeypatch, tmp_path):
    app, client = dashboard
    client.me.return_value = admin_identity()
    password = secrets.token_urlsafe(24)
    credentials = tmp_path / "demo-accounts.json"
    _write_demo_credentials(
        credentials,
        password,
        [
            {
                "label": "Administrator",
                "description": "Admin console only",
                "username": "demo.admin",
                "destination": "admin",
            }
        ],
    )
    monkeypatch.setenv("POLICYPILOT_UI_DEMO_CREDENTIALS", str(credentials))
    app.run()
    app.button(key="demo_login_demo.admin").click().run()
    assert not app.exception
    assert app.session_state["page"] == "admin"
    assert app.session_state["demo_persona"] == "Administrator"
    client.admin_overview.assert_called_once_with("synthetic-session-value")


def test_malformed_demo_credentials_hide_role_switcher(dashboard, monkeypatch, tmp_path):
    app, _ = dashboard
    credentials = tmp_path / "demo-accounts.json"
    credentials.write_text(
        '{"version": 1, "password": "short"}',  # pragma: allowlist secret
        encoding="utf-8",
    )
    credentials.chmod(0o600)
    monkeypatch.setenv("POLICYPILOT_UI_DEMO_CREDENTIALS", str(credentials))
    app.run()
    assert not app.exception
    assert "demo_login_demo.employee" not in {getattr(button, "key", None) for button in app.button}
    assert app.button(key="landing_employee")


def test_group_readable_demo_credentials_are_ignored(dashboard, monkeypatch, tmp_path):
    app, _ = dashboard
    credentials = tmp_path / "demo-accounts.json"
    _write_demo_credentials(
        credentials,
        secrets.token_urlsafe(24),
        [
            {
                "label": "Employee",
                "description": "Public + Employee baseline",
                "username": "demo.employee",
                "destination": "workspace",
            }
        ],
    )
    credentials.chmod(0o644)
    monkeypatch.setenv("POLICYPILOT_UI_DEMO_CREDENTIALS", str(credentials))
    app.run()
    assert not app.exception
    assert "demo_login_demo.employee" not in {getattr(button, "key", None) for button in app.button}


def test_demo_login_failure_stays_on_landing(dashboard, monkeypatch, tmp_path):
    app, client = dashboard
    credentials = tmp_path / "demo-accounts.json"
    _write_demo_credentials(
        credentials,
        secrets.token_urlsafe(24),
        [
            {
                "label": "Employee",
                "description": "Public + Employee baseline",
                "username": "demo.employee",
                "destination": "workspace",
            }
        ],
    )
    monkeypatch.setenv("POLICYPILOT_UI_DEMO_CREDENTIALS", str(credentials))
    client.login.side_effect = DashboardError("throttled")
    app.run()
    app.button(key="demo_login_demo.employee").click().run()
    assert not app.exception
    assert "access_token" not in app.session_state
    assert app.session_state["page"] == "landing"
    assert "Too many attempts" in visible_output(app)


def test_policy_library_unauthorized_returns_to_landing(dashboard):
    app, client = dashboard
    client.policies.side_effect = DashboardError("unauthorized")
    open_login(app)
    sign_in(app)
    assert not app.exception
    assert "access_token" not in app.session_state
    assert app.session_state["page"] == "landing"
    assert "session ended" in visible_output(app).lower()


def test_admin_overview_forbidden_clears_session(dashboard):
    app, client = dashboard
    client.me.return_value = admin_identity()
    client.admin_overview.side_effect = DashboardError("forbidden")
    open_login(app, "admin")
    sign_in(app, "admin")
    assert not app.exception
    assert "access_token" not in app.session_state
    assert app.session_state["page"] == "landing"
    assert "Administrator access could not be confirmed" in visible_output(app)


def test_sample_prompt_populates_question_without_asking(dashboard):
    app, client = dashboard
    open_login(app)
    sign_in(app)
    app.button(key="sample_0").click().run()
    assert not app.exception
    assert app.text_area(key="query_question").value.startswith("How much leave")
    assert not client.query.called


def test_policy_library_failure_does_not_break_workspace(dashboard):
    app, client = dashboard
    client.policies.side_effect = DashboardError("unavailable")
    open_login(app)
    sign_in(app)
    assert not app.exception
    assert "API temporarily unavailable" in visible_output(app)
    assert app.text_area(key="query_question")


def test_policy_library_empty_state(dashboard):
    app, client = dashboard
    client.policies.return_value = ()
    open_login(app)
    sign_in(app)
    assert not app.exception
    assert "No policy documents are currently available" in visible_output(app)


def test_employee_login_routes_to_workspace(dashboard):
    app, client = dashboard
    open_login(app)
    assert app.text_input(key="login_username")
    assert app.text_input(key="login_password").proto.type == TextInput.PASSWORD
    assert app.button(key="back_home")
    assert "Employee portal" in visible_output(app)
    sign_in(app)
    assert not app.exception
    args = client.login.call_args.args
    assert args[0] == "alice"
    assert isinstance(args[1], str) and len(args[1]) > 0
    client.me.assert_called_with("synthetic-session-value")
    assert app.session_state["login_password"] == ""
    assert app.session_state["access_token"] == "synthetic-session-value"  # noqa: S105
    assert app.session_state["page"] == "workspace"
    rendered = visible_output(app)
    assert "EMPLOYEE WORKSPACE" in rendered
    assert "Your policy workspace." in rendered
    assert "Signed in as" in rendered
    assert "YOUR ACCESS" in rendered
    assert "Public" in rendered and "Employee" in rendered
    assert "synthetic-password-fixture" not in rendered
    assert "synthetic-session-value" not in rendered
    assert "Try a question" in rendered
    assert "Annual Leave Policy" in rendered
    assert "Eligibility" in rendered
    client.policies.assert_called_with("synthetic-session-value")
    assert app.text_area(key="query_question")
    assert app.button(key="refresh_permissions")
    assert app.button(key="sign_out")


def test_admin_login_routes_to_read_only_admin_console(dashboard):
    app, client = dashboard
    client.me.return_value = admin_identity()
    open_login(app, "admin")
    assert "Administrator portal" in visible_output(app)
    assert "ADMINISTRATOR SIGN IN" in visible_output(app)
    sign_in(app, "admin")
    assert not app.exception
    client.admin_overview.assert_called_once_with("synthetic-session-value")
    assert app.session_state["page"] == "admin"
    rendered = visible_output(app)
    assert "Administration" in rendered
    assert "ACCESS MATRIX" in rendered
    assert "Effective access at a glance" in rendered
    assert "ACCESS COVERAGE" in rendered
    assert "ACCOUNT DIRECTORY" in rendered
    assert "People with local access" in rendered
    assert "enabled accounts" in rendered
    assert "policy documents" in rendered
    assert "656" in rendered
    assert "alice" in rendered and "admin" in rendered
    assert app.button(key="nav_employee")
    assert app.button(key="admin_employee")
    assert app.button(key="sign_out")


def test_admin_portal_denies_non_admin_and_routes_to_workspace(dashboard):
    app, client = dashboard
    open_login(app, "admin")
    sign_in(app)
    assert not app.exception
    assert not client.admin_overview.called
    assert app.session_state["page"] == "admin"
    rendered = visible_output(app)
    assert "Administrator access required" in rendered
    assert "does not hold the **Admin**" in rendered
    app.button(key="denied_employee").click().run()
    assert not app.exception
    assert app.session_state["page"] == "workspace"
    assert "EMPLOYEE WORKSPACE" in visible_output(app)


@pytest.mark.parametrize(
    "kind", ["unauthorized", "forbidden", "throttled", "unavailable", "invalid"]
)
def test_login_failure_clears_password_and_stays_on_login(dashboard, kind):
    app, client = dashboard
    client.login.side_effect = DashboardError(kind)
    open_login(app)
    sign_in(app)
    assert not app.exception
    assert app.session_state["login_password"] == ""
    assert "access_token" not in app.session_state
    assert app.session_state["page"] == "login"
    assert app.text_input(key="login_username")
    assert app.button(key="sign_in")
    assert not client.me.called


def test_back_to_home_returns_to_landing(dashboard):
    app, _ = dashboard
    open_login(app)
    app.button(key="back_home").click().run()
    assert not app.exception
    assert app.session_state["page"] == "landing"
    assert app.button(key="landing_employee")
    assert not app.text_input


def test_expired_session_returns_to_landing(dashboard):
    app, client = dashboard
    open_login(app)
    sign_in(app)
    client.me.side_effect = DashboardError("unauthorized")
    app.button(key="refresh_permissions").click().run()
    assert not app.exception
    assert "access_token" not in app.session_state
    assert app.session_state["page"] == "landing"
    assert app.button(key="landing_employee")
    assert "session ended" in visible_output(app).lower()


@pytest.mark.parametrize("kind", ["unavailable", "invalid", "throttled"])
def test_failed_refresh_hides_identity_but_preserves_sign_out(dashboard, kind):
    app, client = dashboard
    open_login(app)
    sign_in(app)
    client.me.side_effect = DashboardError(kind)
    app.button(key="refresh_permissions").click().run()
    assert not app.exception
    assert app.session_state["access_token"] == "synthetic-session-value"  # noqa: S105
    assert app.error
    assert app.button(key="sign_out")
    assert app.button(key="retry_identity")
    assert not app.text_area


@pytest.mark.parametrize("failure", [False, True])
def test_logout_returns_to_landing_even_when_server_unavailable(dashboard, failure):
    app, client = dashboard
    open_login(app)
    sign_in(app)
    if failure:
        client.logout.side_effect = DashboardError("unavailable")
    app.button(key="sign_out").click().run()
    assert not app.exception
    client.logout.assert_called_once_with("synthetic-session-value")
    assert "access_token" not in app.session_state
    assert app.session_state["page"] == "landing"
    assert app.button(key="landing_employee")
    if failure:
        assert "could not be confirmed" in visible_output(app)


def test_signed_in_query_renders_answer_and_citations_without_secrets(dashboard):
    app, client = dashboard
    open_login(app)
    sign_in(app)
    client.query.return_value = QueryAnswer(
        "answered",
        "Annual leave is 26 days. [1]",
        (
            QueryCitation(
                chunk_id="chunk-1",
                document_id="doc-1",
                title="Annual Leave Policy",
                section="Eligibility",
                source_url="https://example.invalid/leave",
                snippet="Full-time employees receive 26 days.",
            ),
        ),
    )
    app.text_area(key="query_question").input("How much leave?")
    app.button(key="ask_question").click().run()
    assert not app.exception
    client.query.assert_called_once_with("synthetic-session-value", "How much leave?")
    rendered = visible_output(app)
    assert "Annual leave is 26 days" in rendered
    assert "Annual Leave Policy" in rendered
    assert "Full-time employees receive 26 days" in rendered
    assert "synthetic-session-value" not in rendered


def test_abstained_query_shows_fixed_message_without_citations(dashboard):
    app, client = dashboard
    open_login(app)
    sign_in(app)
    client.query.return_value = QueryAnswer(
        "abstained", "I couldn't find this in the documents available to you.", ()
    )
    app.text_area(key="query_question").input("unknown")
    app.button(key="ask_question").click().run()
    assert not app.exception
    assert app.info
    assert "couldn't find" in app.info[-1].value
    assert "Authorized sources" not in visible_output(app)


def test_source_button_reads_authorized_source(dashboard):
    app, client = dashboard
    open_login(app)
    sign_in(app)
    client.query.return_value = QueryAnswer(
        "answered",
        "Answer [1]",
        (
            QueryCitation(
                "chunk-1",
                "doc-1",
                "Annual Leave Policy",
                "Eligibility",
                "https://example.invalid/leave",
                "Snippet",
            ),
        ),
    )
    client.source.return_value = SourceView(
        chunk_id="chunk-1",
        document_id="doc-1",
        title="Annual Leave Policy",
        section="Eligibility",
        source_url="https://example.invalid/leave",
        text="Full authorized source text.",
    )
    app.text_area(key="query_question").input("leave")
    app.button(key="ask_question").click().run()
    app.button(key="source_1").click().run()
    assert not app.exception
    client.source.assert_called_once_with("synthetic-session-value", "chunk-1")
    assert "Full authorized source text" in visible_output(app)


def test_query_unauthorized_returns_to_landing(dashboard):
    app, client = dashboard
    open_login(app)
    sign_in(app)
    client.query.side_effect = DashboardError("unauthorized")
    app.text_area(key="query_question").input("leave")
    app.button(key="ask_question").click().run()
    assert not app.exception
    assert "access_token" not in app.session_state
    assert app.session_state["page"] == "landing"
    assert app.button(key="landing_employee")


def test_refresh_clears_previous_answer(dashboard):
    app, client = dashboard
    open_login(app)
    sign_in(app)
    client.query.return_value = QueryAnswer(
        "answered",
        "Answer [1]",
        (
            QueryCitation(
                "chunk-1",
                "doc-1",
                "Annual Leave Policy",
                "Eligibility",
                "https://example.invalid/leave",
                "Snippet",
            ),
        ),
    )
    app.text_area(key="query_question").input("leave")
    app.button(key="ask_question").click().run()
    assert "query_answer" in app.session_state
    app.button(key="refresh_permissions").click().run()
    assert "query_answer" not in app.session_state
    assert "Authorized sources" not in visible_output(app)


def test_unreachable_health_is_not_reported_as_ready(dashboard):
    app, client = dashboard
    client.health.return_value = False
    open_login(app)
    sign_in(app)
    assert "API unreachable" in visible_output(app)
    assert "not database/corpus/model readiness" in visible_output(app)


def test_isolated_browser_sessions_start_on_landing(dashboard):
    first, client = dashboard
    open_login(first)
    sign_in(first)
    script = Path(__file__).resolve().parents[1] / "policypilot/ui/app.py"
    second = AppTest.from_file(script, default_timeout=10).run()
    assert not second.exception
    assert "access_token" not in second.session_state
    assert second.button(key="landing_employee")
    assert not second.text_input
    assert first.session_state["access_token"] == client.login.return_value


def test_uncaught_browser_errors_hide_message_type_and_traceback():
    assert config.get_option("client.showErrorDetails") == "none"
    app = AppTest.from_string('raise RuntimeError("synthetic-error-marker")').run()
    assert len(app.exception) == 1
    error = app.exception[0].proto
    assert error.type == ""
    assert not error.stack_trace
    assert "synthetic-error-marker" not in error.message


def test_invalid_operator_configuration_does_not_reveal_value(monkeypatch):
    monkeypatch.setenv("POLICYPILOT_UI_API_BASE_URL", "http://private-canary.invalid")
    app = AppTest.from_function(run_dashboard).run()
    assert not app.exception
    assert app.error
    assert "private-canary" not in visible_output(app)
    assert not app.button
