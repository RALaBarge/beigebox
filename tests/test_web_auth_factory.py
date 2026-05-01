"""
Tests for the AuthProvider ABC, NullAuthProvider, and make_auth() factory in web_auth.py.
"""
import pytest

from beigebox.web_auth import (
    AuthProvider,
    NullAuthProvider,
    GitHubProvider,
    GoogleProvider,
    OAuthUserInfo,
    make_auth,
)


class TestAuthProviderABC:
    def test_null_is_auth_provider(self):
        assert isinstance(NullAuthProvider(), AuthProvider)

    def test_github_is_auth_provider(self):
        p = GitHubProvider(client_id="id", client_secret="secret")
        assert isinstance(p, AuthProvider)

    def test_google_is_auth_provider(self):
        p = GoogleProvider(client_id="id", client_secret="secret", allowed_emails=[])
        assert isinstance(p, AuthProvider)

    def test_cannot_instantiate_base(self):
        with pytest.raises(TypeError):
            AuthProvider()


class TestNullAuthProvider:
    def test_name(self):
        assert NullAuthProvider.name == "none"

    def test_get_authorization_url(self):
        p = NullAuthProvider()
        url, verifier = p.get_authorization_url("http://localhost/cb", "state123")
        assert isinstance(url, str)
        assert isinstance(verifier, str)

    @pytest.mark.asyncio
    async def test_exchange_code_returns_user_info(self):
        p = NullAuthProvider()
        user = await p.exchange_code(code="any", redirect_uri="http://localhost/cb")
        assert isinstance(user, OAuthUserInfo)
        assert user.provider == "none"
        assert "@" in user.email


class TestMakeAuth:
    def test_none_returns_null_provider(self):
        p = make_auth("none")
        assert isinstance(p, NullAuthProvider)
        assert isinstance(p, AuthProvider)

    def test_none_case_insensitive(self):
        assert isinstance(make_auth("NONE"), NullAuthProvider)

    def test_github_returns_github_provider(self):
        p = make_auth("github", client_id="id", client_secret="secret")
        assert isinstance(p, GitHubProvider)
        assert isinstance(p, AuthProvider)

    def test_google_returns_google_provider(self):
        p = make_auth("google", client_id="id", client_secret="secret", allowed_emails=[])
        assert isinstance(p, GoogleProvider)
        assert isinstance(p, AuthProvider)

    def test_unknown_type_raises(self):
        with pytest.raises(ValueError, match="Unknown auth provider"):
            make_auth("saml")
