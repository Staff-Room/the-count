#!/usr/bin/env python3
"""Verify PLAID_CLIENT_ID + PLAID_SECRET match PLAID_ENVIRONMENT."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

from dotenv import load_dotenv

_ROOT = Path(__file__).resolve().parents[1]
load_dotenv(_ROOT / ".env")

import plaid
from plaid.api import plaid_api
from plaid.model.country_code import CountryCode
from plaid.model.institutions_get_request import InstitutionsGetRequest
from plaid.model.institutions_get_request_options import InstitutionsGetRequestOptions

PLAID_ENV = (os.getenv("PLAID_ENV") or os.getenv("PLAID_ENVIRONMENT") or "sandbox").lower()
if PLAID_ENV == "development":
    print(
        "Note: PLAID_ENVIRONMENT=development is deprecated; checking production instead.",
        file=sys.stderr,
    )
    PLAID_ENV = "production"
PLAID_ENVIRONMENTS = {
    "sandbox": plaid.Environment.Sandbox,
    "production": plaid.Environment.Production,
}


def main() -> int:
    client_id = os.getenv("PLAID_CLIENT_ID", "")
    secret = os.getenv("PLAID_SECRET", "")
    if not client_id or not secret:
        print("Missing PLAID_CLIENT_ID or PLAID_SECRET in .env", file=sys.stderr)
        return 1
    if PLAID_ENV not in PLAID_ENVIRONMENTS:
        print(f"Invalid PLAID_ENVIRONMENT={PLAID_ENV!r}", file=sys.stderr)
        return 1

    configuration = plaid.Configuration(
        host=PLAID_ENVIRONMENTS[PLAID_ENV],
        api_key={
            "clientId": client_id,
            "secret": secret,
            "plaidVersion": "2020-09-14",
        },
    )
    client = plaid_api.PlaidApi(plaid.ApiClient(configuration))

    try:
        client.institutions_get(
            InstitutionsGetRequest(
                count=1,
                offset=0,
                country_codes=[CountryCode("US")],
                options=InstitutionsGetRequestOptions(),
            )
        )
    except plaid.ApiException as e:
        body = json.loads(e.body) if e.body else {}
        print(f"Plaid {PLAID_ENV} credentials failed:", file=sys.stderr)
        print(json.dumps(body, indent=2), file=sys.stderr)
        print(
            f"\nUse the {PLAID_ENV.title()} secret from "
            "https://dashboard.plaid.com/developers/keys (not Sandbox).",
            file=sys.stderr,
        )
        return 1

    host = PLAID_ENVIRONMENTS[PLAID_ENV]
    print(f"OK - credentials work for Plaid {PLAID_ENV} ({host})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
