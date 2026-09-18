"""Back-office API: accounts by invitation, uploads, review and validation of protocols.

A separate FastAPI application (port 8100) from the learner one, with its own cookie, its
own roles and credentials for both databases. Never mounted inside the learner app.
"""
