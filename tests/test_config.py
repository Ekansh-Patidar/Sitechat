"""Settings must never print secrets.

A failing test, a logged exception or Sentry can print the settings
object, so keys are SecretStr and render as asterisks.
"""

from app.config import Settings


def test_keys_do_not_appear_when_settings_are_printed():
    secrets = {
        "openai_api_key": "sk-test-openai",
        "pinecone_api_key": "pc-test-pinecone",
        "cohere_api_key": "co-test-cohere",
        "resend_api_key": "re-test-resend",
        "sentry_dsn": "https://key@sentry.example/1",
    }
    settings = Settings(_env_file=None, **secrets)

    printed = repr(settings) + str(settings) + str(settings.model_dump())
    for value in secrets.values():
        assert value not in printed

    # The code that needs a key still gets the real value.
    assert settings.openai_api_key.get_secret_value() == "sk-test-openai"
