"""
Creates the four Attestor demo users directly via the Keycloak Admin REST
API, assigns each their single governance role, generates a strong random
password per user, and writes those passwords to .env.local — which is
gitignored from commit one (see .gitignore).

Why not put fixed passwords in the realm import JSON: that file is meant to
be committed (it's the realm/client config, not secrets), and a static
password sitting in git history is exactly the kind of thing a security
review calls out on day one. Random-per-run keeps the repo itself clean of
credentials even though this is a local demo.

Run with: uv run python scripts/seed_keycloak_users.py
Idempotent: re-running updates the password for existing users rather than
failing on a duplicate-user error.
"""
import secrets
import string
import sys
import time

import httpx

KEYCLOAK_URL = "http://localhost:8080"
REALM = "attestor"
ADMIN_USER = "admin"
ADMIN_PASSWORD = "admin_local_dev_only"  # matches docker-compose default; override via env if changed

USERS = [
    {"username": "owner", "email": "owner@attestor.local", "first_name": "Priya", "last_name": "Nair", "role": "model_owner"},
    {"username": "validator", "email": "validator@attestor.local", "first_name": "Marcus", "last_name": "Webb", "role": "validator"},
    {"username": "mrm_head", "email": "mrm_head@attestor.local", "first_name": "Dana", "last_name": "Cho", "role": "mrm_head"},
    {"username": "auditor", "email": "auditor@attestor.local", "first_name": "Sam", "last_name": "Ellis", "role": "auditor"},
]


def generate_password(length: int = 20) -> str:
    # Alphanumeric only, deliberately. A 20-char alphanumeric password has
    # ~10^35 possible combinations — plenty of entropy for local dev creds.
    # Symbols were tried first and dropped: characters like %, &, =, + are
    # special in shells, URLs, and form-encoded bodies, and a generated
    # password containing them breaks the moment someone pastes it into a
    # curl command without --data-urlencode. Optimizing for "copy-pasteable
    # without surprises" matters more here than squeezing out marginal
    # extra entropy nobody needs for a local demo credential.
    alphabet = string.ascii_letters + string.digits
    return "".join(secrets.choice(alphabet) for _ in range(length))


def wait_for_keycloak(client: httpx.Client, timeout_s: int = 60) -> None:
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        try:
            resp = client.get(f"{KEYCLOAK_URL}/realms/master")
            if resp.status_code == 200:
                return
        except httpx.ConnectError:
            pass
        time.sleep(2)
    raise RuntimeError("Keycloak did not become ready within timeout. Check `docker compose logs keycloak`.")


def get_admin_token(client: httpx.Client) -> str:
    resp = client.post(
        f"{KEYCLOAK_URL}/realms/master/protocol/openid-connect/token",
        data={
            "grant_type": "password",
            "client_id": "admin-cli",
            "username": ADMIN_USER,
            "password": ADMIN_PASSWORD,
        },
    )
    resp.raise_for_status()
    return resp.json()["access_token"]


def find_user(client: httpx.Client, token: str, username: str) -> dict | None:
    resp = client.get(
        f"{KEYCLOAK_URL}/admin/realms/{REALM}/users",
        params={"username": username, "exact": "true"},
        headers={"Authorization": f"Bearer {token}"},
    )
    resp.raise_for_status()
    results = resp.json()
    return results[0] if results else None


def create_or_update_user(client: httpx.Client, token: str, user_spec: dict, password: str) -> str:
    headers = {"Authorization": f"Bearer {token}"}
    existing = find_user(client, token, user_spec["username"])

    if existing is None:
        resp = client.post(
            f"{KEYCLOAK_URL}/admin/realms/{REALM}/users",
            headers=headers,
            json={
                "username": user_spec["username"],
                "email": user_spec["email"],
                "firstName": user_spec["first_name"],
                "lastName": user_spec["last_name"],
                "enabled": True,
                "emailVerified": True,
            },
        )
        resp.raise_for_status()
        user_id = resp.headers["Location"].rstrip("/").split("/")[-1]
    else:
        user_id = existing["id"]

    # Set/reset password
    client.put(
        f"{KEYCLOAK_URL}/admin/realms/{REALM}/users/{user_id}/reset-password",
        headers=headers,
        json={"type": "password", "value": password, "temporary": False},
    ).raise_for_status()

    # Assign realm role (idempotent: fetch role rep, then add)
    role_resp = client.get(
        f"{KEYCLOAK_URL}/admin/realms/{REALM}/roles/{user_spec['role']}",
        headers=headers,
    )
    role_resp.raise_for_status()
    role_rep = role_resp.json()

    client.post(
        f"{KEYCLOAK_URL}/admin/realms/{REALM}/users/{user_id}/role-mappings/realm",
        headers=headers,
        json=[role_rep],
    )  # 204 on success, safe to re-post if already assigned

    return user_id


def main() -> None:
    with httpx.Client(timeout=15.0) as client:
        print("Waiting for Keycloak to be ready...")
        wait_for_keycloak(client)

        token = get_admin_token(client)
        env_lines = ["# Generated by scripts/seed_keycloak_users.py — do not commit, do not edit by hand.\n"]

        for spec in USERS:
            password = generate_password()
            create_or_update_user(client, token, spec, password)
            env_var = f"ATTESTOR_DEMO_PASSWORD_{spec['username'].upper()}"
            env_lines.append(f"{env_var}={password}\n")
            print(f"  seeded user '{spec['username']}' with role '{spec['role']}'")

        with open(".env.local", "w") as f:
            f.writelines(env_lines)

        print("\nDone. Credentials written to .env.local (gitignored).")
        print("Log in at http://localhost:8080/realms/attestor/account with any username above.")


if __name__ == "__main__":
    try:
        main()
    except httpx.HTTPStatusError as exc:
        print(f"Keycloak API error: {exc.response.status_code} {exc.response.text}", file=sys.stderr)
        sys.exit(1)
