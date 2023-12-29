from collections import Counter
from typing import Iterable, TypedDict, cast

import jmapc
import rich
import typer
from jmapc import (
    Comparator,
    EmailQueryFilterCondition,
    MailboxQueryFilterCondition,
    Ref,
)
from jmapc.methods import (
    EmailGet,
    EmailGetResponse,
    EmailQuery,
    EmailSet,
    EmailSetResponse,
    MailboxGet,
    MailboxGetResponse,
    MailboxQuery,
    MailboxSet,
    MailboxSetResponse,
)
from jmapc.models import Email, Mailbox
from pydantic_settings import BaseSettings

app = typer.Typer()


class Settings(BaseSettings):
    jmap_host: str
    jmap_api_token: str


@app.command()
def list_emails():
    settings = Settings()
    client = jmapc.Client.create_with_api_token(
        host=settings.jmap_host, api_token=settings.jmap_api_token
    )
    methods = [
        MailboxQuery(filter=MailboxQueryFilterCondition(name="Inbox", role="inbox")),
    ]
    results = client.request(methods)
    rich.print(results)

    counter = Counter()
    for mailbox_id in results[0].response.ids:
        rich.print(mailbox_id)
        methods = [
            EmailQuery(
                collapse_threads=True,
                filter=EmailQueryFilterCondition(
                    in_mailbox=mailbox_id,
                ),
                sort=[Comparator(property="receivedAt", is_ascending=False)],
            ),
            EmailGet(
                ids=Ref("/ids"), properties=["to", "subject", "from", "mailboxIds"]
            ),
        ]
        results = client.request(methods)
        for email in results[1].response.data:
            if email.to is None:
                continue
            rich.print(
                f"{email.id} - {email.subject} - {email.to} - {email.mailbox_ids}"
            )
            for to in email.to:
                address = to.email.lower()
                counter[address] += 1
    rich.print(email)
    rich.print(counter.most_common())
    rich.print(len(counter))


def get_mailbox_id(
    client: jmapc.Client, name: str, **query_filter_condition_kwargs
) -> str:
    """Gets the id of a single mailbox

    If there are multiple partial name matches then returns the one which exactly matches the name.
    """
    methods = [
        MailboxQuery(
            filter=MailboxQueryFilterCondition(
                name=name, **query_filter_condition_kwargs
            )
        ),
        MailboxGet(ids=Ref("/ids")),
    ]
    results = client.request(methods, raise_errors=True)
    response = cast(MailboxGetResponse, results[1].response)

    mailboxes = [m for m in response.data if m.name == name]
    assert len(mailboxes) == 1, (results, mailboxes)
    mailbox = mailboxes[0]
    assert mailbox.id is not None, (results, mailboxes, mailbox)
    return mailbox.id


def get_inbox_id(client: jmapc.Client) -> str:
    return get_mailbox_id(client, name="Inbox", role="inbox")


class DomainMailboxes(TypedDict):
    """example.com -> aliases"""

    mailbox: Mailbox
    aliases: dict[str, Mailbox]


class ToMailboxes(TypedDict):
    """To -> example.com -> aliases"""

    domains: dict[str, DomainMailboxes]


def get_to_mailboxes(client: jmapc.Client) -> ToMailboxes:
    to_mailboxes: ToMailboxes = {"domains": {}}
    to_mailbox_id = get_mailbox_id(client, name="To")
    methods = [
        MailboxQuery(filter=MailboxQueryFilterCondition(parent_id=to_mailbox_id)),
        MailboxGet(ids=Ref("/ids")),
    ]
    results = client.request(methods)
    response = cast(MailboxGetResponse, results[-1].response)
    for domain_mailbox in response.data:
        assert domain_mailbox.name is not None, (results, domain_mailbox)
        to_mailboxes["domains"][domain_mailbox.name] = {
            "mailbox": domain_mailbox,
            "aliases": {},
        }
        assert domain_mailbox.id is not None, (results, domain_mailbox)
        alias_methods = [
            MailboxQuery(
                filter=MailboxQueryFilterCondition(parent_id=domain_mailbox.id)
            ),
            MailboxGet(ids=Ref("/ids")),
        ]
        alias_results = client.request(alias_methods)
        alias_response = cast(MailboxGetResponse, alias_results[-1].response)
        for alias_mailbox in alias_response.data:
            assert alias_mailbox.name is not None, (
                results,
                domain_mailbox,
                alias_mailbox,
            )
            to_mailboxes["domains"][domain_mailbox.name]["aliases"][
                alias_mailbox.name
            ] = alias_mailbox
    return to_mailboxes


def iterate_emails(
    client: jmapc.Client, mailbox_id: str, ignore_emails: list[str], domains: list[str]
) -> Iterable[Email]:
    methods = [
        EmailQuery(filter=EmailQueryFilterCondition(in_mailbox=mailbox_id)),
        EmailGet(ids=Ref("/ids")),
    ]
    results = client.request(methods)
    response = cast(EmailGetResponse, results[-1].response)

    for email in response.data:
        if email.to is None:
            continue
        if len(email.to) != 1:
            continue
        to = email.to[0]
        assert to.email is not None, (response, email)
        _, domain = to.email.split("@", 1)
        if to.email not in ignore_emails and domain in domains:
            yield email


def make_mailbox(client: jmapc.Client, parent_mailbox_id: str, name: str) -> Mailbox:
    methods = [
        MailboxSet(create=dict(mailbox=Mailbox(name=name, parent_id=parent_mailbox_id)))
    ]
    results = client.request(methods)
    response = cast(MailboxSetResponse, results[-1].response)
    rich.print(response)
    assert response.created is not None, response
    assert response.created["mailbox"] is not None, response
    return response.created["mailbox"]


def move_email(client: jmapc.Client, email: Email, destination_mailbox_id: str) -> None:
    assert email.id is not None
    methods = [
        EmailSet(update={email.id: {"mailboxIds": {destination_mailbox_id: True}}})
    ]
    results = client.request(methods)
    response = cast(EmailSetResponse, results[-1].response)
    rich.print(response)
    assert response.updated is not None, response
    assert email.id in response.updated, response


@app.command()
def sort_emails_by_alias(ignore_email: list[str]):
    """Moves emails from alias@example.com to INBOX.To.[example.com]/[alias]"""

    settings = Settings()
    client = jmapc.Client.create_with_api_token(
        host=settings.jmap_host, api_token=settings.jmap_api_token
    )

    inbox_id = get_inbox_id(client)
    rich.print(("inbox_id", inbox_id))

    to_mailboxes = get_to_mailboxes(client)
    rich.print(("to_mailboxes", to_mailboxes))

    inbox_mailbox_ids = {inbox_id: True}

    i = 0
    for i, email in enumerate(
        iterate_emails(
            client,
            inbox_id,
            ignore_email,
            list(to_mailboxes["domains"].keys()),
        )
    ):
        if email.mailbox_ids != inbox_mailbox_ids:
            rich.print(email)
            continue
        assert email.to is not None, email
        assert email.to[0].email is not None, email
        alias, domain = email.to[0].email.split("@", 1)
        rich.print(alias, domain)
        if domain not in list(to_mailboxes["domains"].keys()):
            rich.print(email)
            continue

        if alias not in to_mailboxes["domains"][domain]["aliases"]:
            rich.print(f"make mailbox {domain}/{alias}")
            alias_mailbox_id = to_mailboxes["domains"][domain]["mailbox"].id
            assert alias_mailbox_id is not None, to_mailboxes["domains"][domain]["mailbox"]
            mailbox = make_mailbox(
                client,
                parent_mailbox_id=alias_mailbox_id,
                name=alias,
            )
            rich.print(mailbox)
            to_mailboxes["domains"][domain]["aliases"][alias] = mailbox
        alias_mailbox_id = to_mailboxes["domains"][domain]["mailbox"].id
        assert alias_mailbox_id is not None, to_mailboxes["domains"][domain]["mailbox"]
        rich.print(f"mv {email.id}/{email.subject} -> {domain}/{alias}")
        move_email(client, email, alias_mailbox_id)

    rich.print(i)


if __name__ == "__main__":
    app()
