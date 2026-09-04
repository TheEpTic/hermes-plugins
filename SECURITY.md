# Security Policy

## Supported versions

Security fixes are made on the current minor release line for each published package.

| component | supported versions |
|---|---|
| `hermes-ssh` | `0.3.x` |
| `hermes-sfw` | `0.2.x` |
| earlier releases | no |

## What to report

Please report vulnerabilities in this repository, its GitHub Actions workflows, or the published `hermes-ssh` and `hermes-sfw` packages. Examples include authentication or authorization bypasses, command injection, unsafe file handling, secret exposure, dependency-confusion paths, and release-pipeline compromise.

`hermes-sfw` is a dependency guard, not a sandbox. Package-manager lifecycle scripts and build backends executing as designed are not vulnerabilities by themselves. A bypass of the plugin's operation restrictions or approval boundary is in scope.

`hermes-ssh` deliberately executes commands on operator-registered remote machines. Risks inherent to granting an agent that authority are not vulnerabilities by themselves. A way to exceed the configured host, credential, or command boundary is in scope.

## What is out of scope

- vulnerabilities solely in Hermes Agent, Socket Firewall Free, OpenSSH, a package manager, or another upstream dependency, unless this repository's integration creates the issue
- social engineering, phishing, and attacks against third-party infrastructure
- denial-of-service testing that disrupts services or consumes significant shared resources
- reports requiring access to data, accounts, or hosts you do not own or have explicit permission to test

## Report privately

Use [GitHub private vulnerability reporting](https://github.com/TheEpTic/hermes-plugins/security/advisories/new) whenever possible. Do **not** open a public issue for a suspected vulnerability.

If GitHub reporting is unavailable, email [nexus@eptic.me](mailto:nexus@eptic.me) with the subject `hermes-plugins security report`.

Include:

- affected package and version, or commit SHA
- a minimal reproduction or proof of concept
- impact and realistic attack preconditions
- any suggested mitigation
- whether and how you would like to be credited

Please give us a reasonable chance to investigate and ship a fix before public disclosure. Do not access, alter, or retain user data beyond what is necessary to demonstrate the issue.

## Handling and disclosure

We aim to acknowledge reports within seven calendar days. We will validate the report, determine scope, coordinate a fix, and publish a GitHub security advisory when appropriate. Credit is optional and only given with the reporter's permission. There is currently no bug bounty program.

## Operational guidance

Run Hermes with least privilege. For `hermes-ssh`, use dedicated non-root accounts and tight machine registration controls. For `hermes-sfw`, treat every allowed dependency install as code that can execute on the host, even when the dependency is not known-malicious.
