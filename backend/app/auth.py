"""Demo sign-in (Backend-owned).

Reporting a barrier needs an account, so a false report can be traced to a person. For the
hackathon these are demo accounts with no passwords; real Dartmouth single sign-on is the next
step. Tokens live in memory, so restarting the server signs everyone out.

Use in an endpoint:
    account: Account = Depends(auth.require_account)      # any signed-in account
    account: Account = Depends(auth.require_facilities)   # facilities staff only
"""
import secrets

from fastapi import Header, HTTPException

from .schemas import Account

DEMO_ACCOUNTS = [
    Account(id="stu-alex", name="Alex (demo student)", role="student"),
    Account(id="stu-jordan", name="Jordan (demo student)", role="student"),
    Account(id="stu-riley", name="Riley (demo student)", role="student"),
    Account(id="fac-sam", name="Sam (demo facilities staff)", role="facilities"),
]
_BY_ID = {a.id: a for a in DEMO_ACCOUNTS}
_tokens: dict[str, str] = {}  # token -> account id


def sign_in(account_id: str) -> tuple[str, Account]:
    account = _BY_ID.get(account_id)
    if account is None:
        raise HTTPException(404, "We don't have that demo account.")
    token = secrets.token_urlsafe(24)
    _tokens[token] = account.id
    return token, account


def account_for(authorization: str | None) -> Account | None:
    if not authorization or not authorization.lower().startswith("bearer "):
        return None
    account_id = _tokens.get(authorization.split(" ", 1)[1].strip())
    return _BY_ID.get(account_id) if account_id else None


def optional_account(authorization: str | None = Header(default=None)) -> Account | None:
    return account_for(authorization)


def require_account(authorization: str | None = Header(default=None)) -> Account:
    account = account_for(authorization)
    if account is None:
        raise HTTPException(401, "Please sign in to report or confirm a barrier.")
    return account


def require_facilities(authorization: str | None = Header(default=None)) -> Account:
    account = require_account(authorization)
    if account.role != "facilities":
        raise HTTPException(403, "Only facilities staff can do this.")
    return account


def display_name(account_id: str) -> str:
    account = _BY_ID.get(account_id)
    return account.name if account else account_id
