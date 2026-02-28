#!/usr/bin/env python3
import argparse
import json
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa


def generate_pair():
    private = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    private_pem = private.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode("utf-8")
    public_pem = private.public_key().public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    ).decode("utf-8")
    return private_pem, public_pem


def main():
    parser = argparse.ArgumentParser(description="Generate RSA keypair JSON for JWT RS256")
    parser.add_argument("--kid", required=True, help="Key ID")
    parser.add_argument("--private-out", default="secrets/jwt_private_keys.generated.json")
    parser.add_argument("--public-out", default="secrets/jwt_public_keys.generated.json")
    args = parser.parse_args()

    private_pem, public_pem = generate_pair()

    with open(args.private_out, "w", encoding="utf-8") as fp:
        json.dump({args.kid: private_pem}, fp)

    with open(args.public_out, "w", encoding="utf-8") as fp:
        json.dump({args.kid: public_pem}, fp)

    print(f"Generated: {args.private_out}, {args.public_out}")


if __name__ == "__main__":
    main()
