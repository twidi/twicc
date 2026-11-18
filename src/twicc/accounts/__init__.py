"""Multi-user accounts: identities, roles and per-project access grants.

Design: docs/plans/2026-11-18-multi-user-design.md.

Layout:

- :mod:`twicc.accounts.principal` — *who* is calling (the owner, or one account).
- :mod:`twicc.accounts.project_access` — *what* a principal may see/operate.
- :mod:`twicc.accounts.service` — account and grant mutations (UI + CLI).
- :mod:`twicc.accounts.google` — Google OpenID Connect sign-in.
"""
