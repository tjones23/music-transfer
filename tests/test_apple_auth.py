from importlib import resources

import jwt
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec

from music_transfer.apple.auth import make_developer_token
from music_transfer.config import AppleSettings


def test_developer_token_is_valid_es256(tmp_path):
    key = ec.generate_private_key(ec.SECP256R1())
    pem = key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption())
    p8 = tmp_path / "AuthKey_TEST.p8"
    p8.write_bytes(pem)
    tok = make_developer_token(AppleSettings(team_id="TEAM123", key_id="KEY456", private_key_path=p8))
    assert jwt.get_unverified_header(tok["token"])["kid"] == "KEY456"
    claims = jwt.decode(tok["token"], key.public_key(), algorithms=["ES256"])
    assert claims["iss"] == "TEAM123" and claims["exp"] == tok["expires_at"]


def test_login_page_is_packaged():
    html = resources.files("music_transfer.apple").joinpath("login.html").read_text(encoding="utf-8")
    assert "__DEVELOPER_TOKEN__" in html and "musickit" in html
