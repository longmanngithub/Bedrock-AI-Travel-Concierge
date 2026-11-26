# Security upgrade

Before deploying migration `0007`, pause intake and drain the old arq worker.
Deploy the API and worker together. New jobs use the `bedrock:arq:json` queue
with JSON serialization. The worker reconciles durable queued itinerary rows
into the new queue. Old Redis-only pending manual deliveries should be drained
or re-requested. Pending Telegram codes must be reissued.

Set a random `JWT_SECRET` of at least 32 bytes and keep `COOKIES_SECURE=true`.
Configure `FORWARDED_ALLOW_IPS` with the reverse proxy's actual trusted IPs or
CIDRs. The edge proxy must replace client-provided forwarding information.
If a CDN stands in front of the proxy, configure the approved CDN proxy ranges
before using any CDN-provided client IP header.

Use a private database connection and enable authenticated TLS with
`sslmode=verify-full` for external PostgreSQL and a `rediss://` DSN for external
Redis. Use `QUEUE_REDIS_URL` for a separately ACL-scoped Redis broker, and
restrict access to the network and queue keyspace to the API and worker. Use Redis 6.2 or later and keep
the broker on a no-eviction policy. The production Compose overlay accepts
existing DSNs so operators can migrate the external services separately.

For GitHub Actions SSH deployments, set `DROPLET_SSH_KNOWN_HOSTS` to the
server's verified OpenSSH host key entry. On a custom port, use `[host]:port`.
The workflow deploys only the successful CI revision that is still at the
tip of `main`.

On upgrade, completion email is disabled until the user opts in again, and
Telegram group destinations are disconnected. Users can re-enable delivery
from Settings. Clear All removes chat conversations. Saved itinerary records
remain in PostgreSQL until explicitly deleted, and Redis job snapshots
expire after one hour. Legacy browser history can be imported from Settings
after confirming ownership. An old browser identifier alone cannot claim
anonymous itinerary records.

Review existing log access and retention before rollout. Rotate affected
Telegram, Maps, and travel-provider credentials if logs previously captured
their request URLs. Review already linked Google accounts independently if
you suspect an earlier unauthorized link.

CI and the backend image install from `backend/requirements.lock` with
hash checks. Regenerate it from `backend/requirements.txt` with
`uv pip compile requirements.txt --python-version 3.12 --universal --generate-hashes --no-header -o requirements.lock`
from the backend directory after reviewing dependency changes.

Chroma stays embedded in the worker with no HTTP server. CI excludes four
open Chroma server and RBAC advisories from the dependency gate. Re-evaluate
those exclusions before introducing a Chroma server or when a patched
release becomes available.
