# Status

NestOps owns Homepage, console and the optional household relay in compose.yml. Authentication and the shared gateway are separate Compose projects, with optional deployment examples in compose.auth.yml and compose.gateway.yml. Existing OIDC providers can be reused.

The private deployment has been split without changing identity data, application code or UI. Public examples use an explicit external network. Source provenance and offline regression tests are validated; fresh generic installation, reboot recovery and notification inbox delivery remain environment-specific acceptance work.
