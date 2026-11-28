"""Who may change which settings (.scratch/settings/spec.md "作用域与权限 (S1, S2, S5, S6)";
"Testing Decisions" 权限; decisions.md S1, S5, S6, S9, "建 settings 工单时主会话定的细节" 1).

Written by ticket 157. The tests marked TICKET_159 are xfail(strict) until ticket 159 makes them
pass and removes the marker, changing nothing else.

Contract 1, the permission check (`data_agent.settings.access`, human-reviewed with ticket 157):

- `Identity(user, workspace=None, org_admin=False)`: `user` a platform user id
  (`slack:U0ALICE01`, contract 2), `workspace` the scope id of the workspace they signed in from
  (contract 3). `org_admin` is set for a session of `DATA_AGENT_ADMIN_TOKEN` and for local mode;
  anyone `[server].org_admins` names is an organisation admin too.
- `Access(config, slack, clock=time.monotonic)`: `slack` maps a Slack workspace's scope id to an
  async Slack Web API client (slack_sdk's `AsyncWebClient` or a stand-in with the same
  `users_info(user=...)` and `conversations_members(channel=...)`); `clock` gives seconds.
- `await Access.can_edit(identity, scope, key) -> bool`, by spec S1's table:
  - org layer: organisation admins only;
  - a Slack workspace layer, and a Slack channel layer's access and spend keys: the workspace's
    admins and owners (`users.info` `is_admin` or `is_owner`);
  - a Slack channel layer's behaviour keys (`instructions.*`, `limits.effort`,
    `chat.require_mention`; test_settings_resolve.py lists the kinds): the channel's members
    (`conversations.members`), or only admins and owners with `[policy].behavior_admins_only`;
  - a personal layer: its own person, behaviour keys only;
  - a Discord or Telegram workspace layer, and their channels' access and spend keys:
    organisation admins only;
  - a deployment key: no one.
- What Slack says of a person (`users.info`) and of a channel's members (`conversations.members`)
  is kept `CACHE_SECONDS` (300): within that no second call for the same person or channel; after
  it, asked again. No role or member table is stored (S9).

The admin token as a bearer (detail 1): `Authorization: Bearer <DATA_AGENT_ADMIN_TOKEN>` is the
organisation admin; `GET /api/me` (contract 5, test_settings_login.py) says so.
"""

import asyncio
from pathlib import Path
from typing import Any

import pytest
from settings_support import (
    ADMIN,
    ADMIN_TOKEN,
    ADMIN_TOKEN_ENV,
    ALICE,
    BOB,
    DISCORD_CHANNEL,
    DISCORD_GUILD,
    DISCORD_USER,
    ENG,
    EVE,
    ORG,
    OWNER,
    ROOT,
    SALES,
    TELEGRAM_BOT,
    TELEGRAM_GROUP,
    TELEGRAM_USER,
    FakeClock,
    FakeSlack,
    channel,
    cloud_config_text,
    cloud_env,
    https_client,
    load_config,
    me,
    open_store,
    platform_user,
    served,
    settings_attr,
    shop_config,
    sqlite_tables,
    stop_sessions,
    user,
    workspace,
)

TICKET_159 = pytest.mark.xfail(
    strict=True, raises=AssertionError, reason="ticket 159: the permission checks are not built yet"
)

ACCESS_KEY = "roles.main"
BEHAVIOR_KEY = "instructions.text"
ORG_ADMINS = [platform_user(ROOT), platform_user(DISCORD_USER, "discord")]
ORG_ADMINS += [platform_user(TELEGRAM_USER, "telegram")]


def config_of(tmp_path: Path, policy: str = "") -> Any:
    admins = ", ".join(f'"{a}"' for a in ORG_ADMINS)
    return load_config(
        [shop_config(tmp_path, f"[server]\norg_admins = [{admins}]\n\n{policy}")], {}
    )


def checker(tmp_path: Path, slack: FakeSlack, clock: FakeClock, policy: str = "") -> Any:
    access = settings_attr("access", "Access")
    return access(config_of(tmp_path, policy), {workspace(): slack}, clock=clock)


def slack_person(person: str) -> Any:
    return settings_attr("access", "Identity")(platform_user(person), workspace())


def can(access: Any, identity: Any, scope: str, key: str) -> bool:
    return bool(asyncio.run(access.can_edit(identity, scope, key)))


async def acan(access: Any, identity: Any, scope: str, key: str) -> bool:
    return bool(await access.can_edit(identity, scope, key))


def table(access: Any, scope: str, key: str, people: dict[str, Any]) -> dict[str, bool]:
    return {name: can(access, identity, scope, key) for name, identity in people.items()}


def slack_people() -> dict[str, Any]:
    return {name: slack_person(name) for name in (ROOT, ADMIN, OWNER, ALICE, BOB, EVE)}


@TICKET_159
def test_the_org_layer_is_the_org_admins(tmp_path: Path) -> None:
    access = checker(tmp_path, FakeSlack(), FakeClock())
    identity = settings_attr("access", "Identity")
    token = identity(None, None, org_admin=True)
    for key in (ACCESS_KEY, BEHAVIOR_KEY):
        assert table(access, ORG, key, slack_people()) == {
            ROOT: True,
            ADMIN: False,
            OWNER: False,
            ALICE: False,
            BOB: False,
            EVE: False,
        }, key
        assert can(access, token, ORG, key)


@TICKET_159
def test_a_slack_workspace_layer_is_its_admins_and_owners(tmp_path: Path) -> None:
    access = checker(tmp_path, FakeSlack(), FakeClock())
    people = {name: slack_person(name) for name in (ADMIN, OWNER, ALICE, EVE)}
    for key in (ACCESS_KEY, BEHAVIOR_KEY, "sandbox.network", "chat.sources"):
        assert table(access, workspace(), key, people) == {
            ADMIN: True,
            OWNER: True,
            ALICE: False,
            EVE: False,
        }, key


@TICKET_159
def test_a_slack_channel_s_access_keys_are_admins_and_its_behaviour_keys_members(
    tmp_path: Path,
) -> None:
    access = checker(tmp_path, FakeSlack(), FakeClock())
    people = {name: slack_person(name) for name in (ADMIN, OWNER, ALICE, BOB, EVE)}
    for key in (ACCESS_KEY, "limits.max_budget_usd", "chat.mcp", "skills.disabled"):
        assert table(access, channel(SALES), key, people) == {
            ADMIN: True,
            OWNER: True,
            ALICE: False,
            BOB: False,
            EVE: False,
        }, key
    members = {name: slack_person(name) for name in (ALICE, BOB, EVE)}
    for key in (BEHAVIOR_KEY, "limits.effort", "chat.require_mention"):
        assert table(access, channel(SALES), key, members) == {
            ALICE: True,
            BOB: True,
            EVE: False,
        }, key
    # EVE is a member of ENG, not of SALES.
    assert can(access, slack_person(EVE), channel(ENG), BEHAVIOR_KEY)


@TICKET_159
def test_the_policy_can_keep_behaviour_keys_to_admins(tmp_path: Path) -> None:
    access = checker(tmp_path, FakeSlack(), FakeClock(), "[policy]\nbehavior_admins_only = true\n")
    people = {name: slack_person(name) for name in (ADMIN, ALICE, BOB)}
    assert table(access, channel(SALES), BEHAVIOR_KEY, people) == {
        ADMIN: True,
        ALICE: False,
        BOB: False,
    }


@TICKET_159
def test_a_personal_layer_is_its_own_person_and_behaviour_only(tmp_path: Path) -> None:
    access = checker(tmp_path, FakeSlack(), FakeClock())
    assert can(access, slack_person(ALICE), user(ALICE), BEHAVIOR_KEY)
    assert can(access, slack_person(ALICE), user(ALICE), "limits.effort")
    assert not can(access, slack_person(ALICE), user(ALICE), ACCESS_KEY)
    assert not can(access, slack_person(ALICE), user(ALICE), "sandbox.network")
    assert not can(access, slack_person(BOB), user(ALICE), BEHAVIOR_KEY)
    assert not can(access, slack_person(ADMIN), user(ALICE), BEHAVIOR_KEY)


@TICKET_159
def test_no_one_changes_a_deployment_key(tmp_path: Path) -> None:
    access = checker(tmp_path, FakeSlack(), FakeClock())
    root = slack_person(ROOT)
    assert can(access, root, ORG, ACCESS_KEY)
    for key in ("sources.shop.path", "server.org_admins", "policy.network", "models.x.base_url"):
        assert not can(access, root, ORG, key), key


@TICKET_159
def test_discord_and_telegram_workspaces_and_access_keys_are_the_org_admins(
    tmp_path: Path,
) -> None:
    access = checker(tmp_path, FakeSlack(), FakeClock())
    identity = settings_attr("access", "Identity")
    discord_admin = identity(
        platform_user(DISCORD_USER, "discord"), workspace(DISCORD_GUILD, "discord")
    )
    discord_member = identity(
        platform_user("990002", "discord"), workspace(DISCORD_GUILD, "discord")
    )
    telegram_admin = identity(
        platform_user(TELEGRAM_USER, "telegram"), workspace(TELEGRAM_BOT, "telegram")
    )
    telegram_member = identity(platform_user("43", "telegram"), workspace(TELEGRAM_BOT, "telegram"))
    cases = [
        (discord_admin, discord_member, workspace(DISCORD_GUILD, "discord")),
        (discord_admin, discord_member, channel(DISCORD_CHANNEL, DISCORD_GUILD, "discord")),
        (telegram_admin, telegram_member, workspace(TELEGRAM_BOT, "telegram")),
        (telegram_admin, telegram_member, channel(TELEGRAM_GROUP, TELEGRAM_BOT, "telegram")),
    ]
    for admin, member, scope in cases:
        assert can(access, admin, scope, ACCESS_KEY), scope
        assert not can(access, member, scope, ACCESS_KEY), scope
    for admin, member, scope in cases[::2]:
        assert can(access, admin, scope, BEHAVIOR_KEY), scope
        assert not can(access, member, scope, BEHAVIOR_KEY), scope


@TICKET_159
async def test_slack_answers_are_kept_five_minutes_and_a_revoked_admin_loses_the_right(
    tmp_path: Path,
) -> None:
    slack = FakeSlack()
    clock = FakeClock()
    access = checker(tmp_path, slack, clock)
    admin, alice = slack_person(ADMIN), slack_person(ALICE)
    assert await acan(access, admin, workspace(), ACCESS_KEY)
    assert await acan(access, alice, channel(SALES), BEHAVIOR_KEY)
    clock.advance(120)
    assert await acan(access, admin, channel(SALES), ACCESS_KEY)
    assert await acan(access, alice, channel(SALES), BEHAVIOR_KEY)
    assert slack.count("users.info", user=ADMIN) == 1
    assert slack.count("conversations.members", channel=SALES) == 1

    slack.people[ADMIN].is_admin = False
    slack.members[SALES].remove(ALICE)
    clock.advance(170)  # 290 s since Slack was asked: still the cached answer
    assert await acan(access, admin, workspace(), ACCESS_KEY)
    assert slack.count("users.info", user=ADMIN) == 1
    clock.advance(20)  # 310 s: asked again
    assert not await acan(access, admin, workspace(), ACCESS_KEY)
    assert not await acan(access, alice, channel(SALES), BEHAVIOR_KEY)
    assert slack.count("users.info", user=ADMIN) == 2
    assert slack.count("conversations.members", channel=SALES) == 2


@TICKET_159
def test_no_role_or_member_table_is_stored(tmp_path: Path) -> None:
    config = config_of(tmp_path)
    db = tmp_path / "settings.sqlite"
    open_store(db, config)
    access = checker(tmp_path / "a", FakeSlack(), FakeClock())
    assert can(access, slack_person(ADMIN), workspace(), ACCESS_KEY)
    assert can(access, slack_person(ALICE), channel(SALES), BEHAVIOR_KEY)
    tables = sqlite_tables(db)
    assert not [t for t in tables if any(w in t.lower() for w in ("role", "member", "admin"))], (
        tables
    )


@TICKET_159
def test_the_admin_token_as_a_bearer_is_the_org_admin(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    stop_sessions(monkeypatch, tmp_path / "home")
    cloud_env(monkeypatch, slack=False)
    monkeypatch.setenv(ADMIN_TOKEN_ENV, ADMIN_TOKEN)
    config = shop_config(tmp_path, cloud_config_text(slack=False))
    with served(tmp_path / "srv", monkeypatch, capsys, ["--config", str(config)]) as server:
        client = https_client(server.app())
        body = me(client, headers={"Authorization": f"Bearer {ADMIN_TOKEN}"})
        assert body.get("org_admin") is True, body
        response = client.get("/api/me", headers={"Authorization": "Bearer wrong"})
        assert response.status_code == 401, response.text
