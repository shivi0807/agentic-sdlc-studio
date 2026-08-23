from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, cast
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from google.auth.transport.requests import Request as GoogleRequest
from google.oauth2 import id_token as google_id_token


class GoogleOAuthError(ValueError):
    pass


@dataclass(frozen=True)
class GoogleIdentity:
    email: str
    display_name: str


@dataclass(frozen=True)
class GoogleOAuthClient:
    client_id: str
    client_secret: str
    redirect_uri: str

    def authorization_url(self, state: str, nonce: str) -> str:
        query = urlencode(
            {
                "client_id": self.client_id,
                "redirect_uri": self.redirect_uri,
                "response_type": "code",
                "scope": "openid email profile",
                "state": state,
                "nonce": nonce,
                "prompt": "select_account",
            }
        )
        return f"https://accounts.google.com/o/oauth2/v2/auth?{query}"

    def exchange_code(self, code: str, expected_nonce: str) -> GoogleIdentity:
        body = urlencode(
            {
                "code": code,
                "client_id": self.client_id,
                "client_secret": self.client_secret,
                "redirect_uri": self.redirect_uri,
                "grant_type": "authorization_code",
            }
        ).encode("ascii")
        request = Request(
            "https://oauth2.googleapis.com/token",
            data=body,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            method="POST",
        )
        try:
            with urlopen(request, timeout=10) as response:  # noqa: S310 - fixed Google endpoint
                payload = json.loads(response.read())
        except Exception as error:
            raise GoogleOAuthError("Google sign-in could not be completed") from error
        token = payload.get("id_token")
        if not isinstance(token, str):
            raise GoogleOAuthError("Google sign-in did not return an identity token")
        return self._verified_identity(token, expected_nonce)

    def _verified_identity(self, token: str, expected_nonce: str) -> GoogleIdentity:
        try:
            verify = cast(
                Callable[[str, GoogleRequest, str], dict[str, Any]],
                google_id_token.verify_oauth2_token,
            )
            claims = verify(token, GoogleRequest(), self.client_id)
        except Exception as error:
            raise GoogleOAuthError("Google identity token could not be verified") from error
        if claims.get("nonce") != expected_nonce or claims.get("email_verified") is not True:
            raise GoogleOAuthError("Google identity verification failed")
        email = claims.get("email")
        if not isinstance(email, str) or not email:
            raise GoogleOAuthError("Google account has no verified email address")
        name = claims.get("name")
        return GoogleIdentity(email=email, display_name=name if isinstance(name, str) else email)
