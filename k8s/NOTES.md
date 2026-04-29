# UniFi Daily Briefing, Kubernetes Notes

## Shape

This app follows the plain YAML + Kustomize pattern used in `bartos-cloud-gitops`:

- one namespace: `unifi-daily-briefing`
- one web `Deployment`
- two `CronJob` resources using the same image
- one Longhorn PVC for the SQLite database
- one ingress at `unifi-daily-briefing.lab.bartos.media`

## Image

Current manifests expect:

- `ghcr.io/bmobytes/unifi-daily-briefing:latest`

That image does not appear to be published from this workspace yet. Build and publish the image before applying these manifests in-cluster.

## Required secret keys

Create a Kubernetes Secret named `unifi-daily-briefing-secrets` in the `unifi-daily-briefing` namespace with these keys:

- `UDB_UNIFI_BASE_URL`
- `UDB_UNIFI_USERNAME`
- `UDB_UNIFI_PASSWORD`
- `UDB_UNIFI_API_KEY`
- `UDB_DISCORD_WEBHOOK_URL`
- `UDB_DISCORD_BOT_TOKEN`

Use either classic auth with username and password, or API key auth. The current cluster deployment is intended to use API key auth by default. Do not set both unless you intentionally want classic auth to remain the default. `secret.example.yaml` documents the field names only and should not be applied as-is.

## Brain writer

The app code supports writing reports into the Rackshack Brain, but this manifest set does not mount a Brain-backed writable path yet. `UDB_BRAIN_REPORTS_DIR` is left blank until the cluster-side storage path for the Brain is decided.

## Sample commands

```bash
kubectl apply -k k8s
kubectl -n unifi-daily-briefing get pods,cronjobs,ingress,pvc
```
