# Status

Initial source package: official Homepage plus JS/CSS extension, monitoring backend, optional household relay, gateway and optional Authelia in one Compose; Linux and macOS collectors included.

Source provenance and offline tests are validated. The existing private deployment has been migrated to a shared Compose and checked with nine collectors, authenticated UI and anonymous API rejection. Generic installation examples require user-specific domain, TLS and identity configuration; a clean generic Docker deployment is a separate acceptance step.

The public repository does not contain private deployment facts, monitor history or credentials. Reboot recovery, notification inbox delivery and a fresh generic installation must be verified in the target environment.
