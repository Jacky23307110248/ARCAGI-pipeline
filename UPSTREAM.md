# External harness dependencies

ARCAGI Pipeline contains its own generic Tycho and Duck adapter code. The upstream harness repositories remain external runtime dependencies so that their example runs, game caches, and historical outputs are not exposed through the clean main snapshot.

| Backend | Expected revision | Local runtime input |
|---|---|---|
| Tycho | `f68912a764372ead0a610db2e1c011d41ce5197e` | Pass the checkout path using `harness-prepare --repo` and the non-secret run configuration using `--config`. |
| Duck | `7652836056c59e044f093e3c13ed7438c814169e` | Pass the checkout path using `harness-prepare --backend duck --repo`, plus the game environments directory. |

The adapters validate the local checkout and record its revision and source fingerprint where supported. They do not fetch or update dependencies. Users must obtain the upstream projects under their own applicable licenses and configure the local paths explicitly. Do not place credentials in repository configuration or run manifests; pass them through process environment variables.