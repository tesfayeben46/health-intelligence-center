# Disabled workflows

`deploy.yml` (redeploy on push to `main` or `develop`) and `provision.yml` (manual Ansible bring-up from a bare host) run on self-hosted runners attached to a target k3s host. They are kept here, outside `.github/workflows/`, so GitHub never executes them in this repository.

To use them for a deployment of your own:

- register a self-hosted runner on the target host with the labels the jobs request (`self-hosted, nhdw` for `deploy.yml`; `self-hosted, nhdw-control` for `provision.yml`);
- set what they read: repository variables `NHDW_HOSTBASE` and `NHDW_SECRETS_FILE`, and secrets `NHDW_KUBECONFIG_PATH`, `NHDW_SECRETS_YAML` (base64 `secrets.yaml` for `provision.yml`) and `NHDW_SSH_KEY` (fleet provisioning only);
- protect the deploying branches;
- move the files back into `.github/workflows/`.

Both workflows share the `nhdw-cluster` concurrency group, so a provision and a redeploy never drive the cluster at the same time.
