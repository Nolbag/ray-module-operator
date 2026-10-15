# ray-module-operator

Module operator for the Ray component in OpenDataHub. Manages the lifecycle of the
KubeRay operator (install, upgrade, status, removal) as part of the ODH modular architecture.

## Overview

This operator reconciles a `Ray` custom resource (`components.platform.opendatahub.io/v1alpha1`)
and is deployed by the ODH platform operator when the Ray module is enabled.

**Epic:** [RHOAIENG-62503](https://redhat.atlassian.net/browse/RHOAIENG-62503)
**Feature:** [RHAISTRAT-1064](https://redhat.atlassian.net/browse/RHAISTRAT-1064)

## Development

### Prerequisites

- Go 1.25+
- Docker or Podman

### Build

```sh
make build
```

### Test

```sh
make test
```

### Lint

```sh
make lint
```

### Docker Build

```sh
make docker-build IMG=quay.io/opendatahub/odh-ray-module-operator:latest
```

### Operand manifests

The Ray module owns the KubeRay operand manifests. Platform installation
manifests contain only the module operator; the image carries the selected
KubeRay snapshot under `/opt/manifests/kuberay`.

Normal builds use the committed snapshots under `opt/manifests/{odh,rhoai}`.
Run `make update-manifests` in a connected environment to refresh them from
the pinned KubeRay commits in `hack/manifests-sources.yaml`. This file is the
single source of truth for pins used by both manual downloads and automation.

### Automated manifest updates

The **Sync KubeRay manifests** workflow in `opendatahub-io/ray-module-operator`
opens an update PR when runtime manifest changes reach downstream KubeRay.
The PR refreshes both snapshots, keeping their separate sources:

- ODH: `opendatahub-io/kuberay`, branch `dev`.
- RHOAI: `red-hat-data-services/kuberay`, branch `main`.

The downstream snapshot must differ before either snapshot or pin advances.
Code-only changes and changes to excluded samples, tests, or README files do not
produce a PR. The workflow fetches current branch heads, pins their exact SHAs,
and validates both OpenShift overlays. Repeated events update the same open PR.
A new PR sends its URL to the team's Slack channel; updates to that PR do not
repeat the notification. The PR updates both snapshots and their pins together;
there are no duplicate pins in the download script. Normal PR CI runs using a
GitHub App token and verifies that both snapshots reproduce their pinned
commits. Once configured, the routine human step is reviewing and merging PRs.

To enable the event-driven trigger:

1. Install a GitHub App on `opendatahub-io/ray-module-operator` with repository
   **Contents: read and write** and **Pull requests: read and write** permissions.
2. Configure `MANIFESTS_SYNC_APP_ID` and `MANIFESTS_SYNC_APP_PRIVATE_KEY` as Actions
   secrets in both `opendatahub-io/ray-module-operator` and
   `red-hat-data-services/kuberay`. The downstream workflow uses the App's
   installation on the ODH Ray module repository to send the event.
3. Configure `MANIFESTS_SYNC_SLACK_WEBHOOK_URL` in both repositories with a Slack
   incoming webhook for the team's channel. The sender and receiver also report
   workflow failures to this channel.
4. Copy [`hack/ci/kuberay-manifests-changed.yaml`](hack/ci/kuberay-manifests-changed.yaml)
   to `.github/workflows/notify-ray-module.yaml` in
   `red-hat-data-services/kuberay` and merge it into `main`.
5. Merge this repository's receiver workflow into its default branch. GitHub
   requires it there to receive `repository_dispatch` events.

There is no scheduled polling. Downstream pushes affecting
`ray-operator/config/**` send an event to this repository, with retries for
transient dispatch failures. Merging the receiver workflow into `main` runs an
initial check automatically; changes to its configuration or scripts also run
a check. Use **Actions → Sync KubeRay manifests → Run workflow** to check
manually if needed. The downstream sender also supports **Run workflow**.
Without the downstream sender or an equivalent webhook service, this
repository cannot receive push events from another repo.

For the same check locally, run `make sync-manifests` (Python 3 and network
access required). To reproduce the reviewed versions, run
`make update-manifests`; it reads the current pins without advancing them.
`make verify-operand-manifests` checks both snapshots against those pins without
changing files. These commands do not open PRs locally. Normal builds continue
to use the committed snapshots without network access or a dependency on yq.

## License

Copyright 2026.

Licensed under the Apache License, Version 2.0 (the "License");
you may not use this file except in compliance with the License.
You may obtain a copy of the License at

    http://www.apache.org/licenses/LICENSE-2.0

Unless required by applicable law or agreed to in writing, software
distributed under the License is distributed on an "AS IS" BASIS,
WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
See the License for the specific language governing permissions and
limitations under the License.
