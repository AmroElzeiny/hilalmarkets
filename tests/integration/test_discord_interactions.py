import json

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from pydantic import SecretStr


async def test_discord_interaction_signature_verification(test_context):
    private_key = Ed25519PrivateKey.generate()
    public_bytes = private_key.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
    test_context["settings"].discord_webhook_public_key = SecretStr(public_bytes.hex())
    payload = json.dumps({"type": 1}, separators=(",", ":")).encode()
    timestamp = "1781438400"
    signature = private_key.sign(timestamp.encode("ascii") + payload).hex()

    invalid = await test_context["client"].post(
        "/api/v1/discord/interactions",
        content=payload,
        headers={
            "Content-Type": "application/json",
            "X-Signature-Ed25519": "00" * 64,
            "X-Signature-Timestamp": timestamp,
        },
    )
    assert invalid.status_code == 401

    valid = await test_context["client"].post(
        "/api/v1/discord/interactions",
        content=payload,
        headers={
            "Content-Type": "application/json",
            "X-Signature-Ed25519": signature,
            "X-Signature-Timestamp": timestamp,
        },
    )
    assert valid.status_code == 200
    assert valid.json() == {"type": 1}
