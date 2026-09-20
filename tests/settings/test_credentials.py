from __future__ import annotations

import pickle
from pathlib import Path

import pytest

from galgame_news.settings import (
    CredentialStore,
    InMemoryCredentialBackend,
    KeyringCredentialBackend,
)


def test_credentials_use_injected_backend_and_never_write_to_settings_files(tmp_path: Path):
    backend = InMemoryCredentialBackend()
    store = CredentialStore(backend, service_name="test-galgame-news")

    store.set("brave_api_key", "brave-secret-123")
    store.set("x_bearer_token", "x-secret-456")

    assert store.get("brave_api_key") == "brave-secret-123"
    assert store.get("x_bearer_token") == "x-secret-456"
    assert list(tmp_path.iterdir()) == []
    assert "brave-secret-123" not in repr(store)
    assert "x-secret-456" not in repr(store)
    assert "brave-secret-123" not in repr(backend)
    assert "x-secret-456" not in repr(backend)


def test_credential_bundle_repr_is_redacted():
    backend = InMemoryCredentialBackend()
    store = CredentialStore(backend)
    store.set_brave_api_key("brave-secret")
    store.set_x_bearer_token("x-secret")

    bundle = store.read()

    assert bundle.brave_api_key == "brave-secret"
    assert bundle.x_bearer_token == "x-secret"
    assert "brave-secret" not in repr(bundle)
    assert "x-secret" not in repr(bundle)


def test_credentials_can_be_deleted_without_exposing_secret():
    backend = InMemoryCredentialBackend()
    store = CredentialStore(backend)
    store.set("brave_api_key", "secret")
    store.delete("brave_api_key")
    assert store.get("brave_api_key") is None


def test_keyring_backend_is_lazy_and_injectable():
    class FakeKeyring:
        def __init__(self):
            self.values = {}

        def get_password(self, service, username):
            return self.values.get((service, username))

        def set_password(self, service, username, password):
            self.values[(service, username)] = password

        def delete_password(self, service, username):
            self.values.pop((service, username), None)

    fake = FakeKeyring()
    backend = KeyringCredentialBackend(keyring_module=fake)
    store = CredentialStore(backend)
    store.set("x_bearer_token", "secret")
    assert fake.values
    assert store.get("x_bearer_token") == "secret"


def test_credential_store_and_in_memory_backend_reject_pickle():
    backend = InMemoryCredentialBackend()
    store = CredentialStore(backend)
    store.set("brave_api_key", "pickle-secret")

    for value in (backend, store):
        with pytest.raises(TypeError, match="pickle"):
            pickle.dumps(value)
