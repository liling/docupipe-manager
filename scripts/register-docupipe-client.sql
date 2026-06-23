-- Register docupipe-prod OAuth2 business client in xinyi-platform.
-- Run against the xinyi schema after xinyi-platform is deployed.
--
-- Usage:
--   psql $DATABASE_URL -f scripts/register-docupipe-client.sql
--
-- The redirect_uri must match DOCUPIPE_MANAGER_OAUTH_REDIRECT_URI in docupipe-manager's .env.
-- The client_secret must match DOCUPIPE_MANAGER_OAUTH_CLIENT_SECRET.

INSERT INTO xinyi.business_clients (id, client_id, client_secret, redirect_uri, created_at)
VALUES (
    gen_random_uuid(),
    'docupipe-prod',
    crypt('<CHANGE-ME>', gen_salt('bf')),
    'http://localhost:8002/auth/callback',
    now()
)
ON CONFLICT (client_id) DO NOTHING;
